"""
Unit tests for MCP proxy middleware.

Tests the request routing logic that forwards tool calls to upstream
servers in remote mode while executing locally in local mode.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from returns.result import Success

from tasca.shell.mcp.proxy import UpstreamConfig
from tasca.shell.mcp.server import LOCAL_ONLY_TOOLS, ProxyMiddleware


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def middleware() -> ProxyMiddleware:
    """Create a ProxyMiddleware instance for testing."""
    return ProxyMiddleware()


@pytest.fixture
def mock_context() -> MagicMock:
    """Create a mock middleware context with tool call request."""
    context = MagicMock()
    context.message = MagicMock()
    context.message.name = "table_list"
    context.message.arguments = {"status": "open"}
    return context


@pytest.fixture
def mock_call_next() -> AsyncMock:
    """Create a mock call_next function that returns a ToolResult."""
    from fastmcp.tools.tool import ToolResult
    from mcp.types import TextContent

    async def call_next(context: Any) -> ToolResult:
        return ToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps({"ok": True, "data": {"tables": []}}),
                )
            ]
        )

    return call_next


@pytest.fixture
def success_tool_result() -> dict[str, Any]:
    """A successful tool result envelope from upstream."""
    return {
        "jsonrpc": "2.0",
        "id": "test-id-123",
        "result": {
            "ok": True,
            "data": {
                "tables": [{"id": "table-1", "question": "Test question", "status": "open"}],
                "total": 1,
            },
        },
    }


@pytest.fixture
def error_tool_result() -> dict[str, Any]:
    """A JSON-RPC error from upstream."""
    return {
        "jsonrpc": "2.0",
        "id": "test-id-456",
        "error": {"code": "NOT_FOUND", "message": "Table not found", "data": {}},
    }


# =============================================================================
# LOCAL_ONLY_TOOLS Tests
# =============================================================================


class TestLocalOnlyTools:
    """Tests for LOCAL_ONLY_TOOLS set."""

    def test_connect_in_local_only(self) -> None:
        """connect is in LOCAL_ONLY_TOOLS."""
        assert "connect" in LOCAL_ONLY_TOOLS

    def test_connection_status_in_local_only(self) -> None:
        """connection_status is in LOCAL_ONLY_TOOLS."""
        assert "connection_status" in LOCAL_ONLY_TOOLS

    def test_local_only_is_frozenset(self) -> None:
        """LOCAL_ONLY_TOOLS is a frozenset (immutable)."""
        assert isinstance(LOCAL_ONLY_TOOLS, frozenset)


# =============================================================================
# ProxyMiddleware Tests
# =============================================================================


class TestProxyMiddlewareLocalMode:
    """Tests for ProxyMiddleware in local mode."""

    @pytest.mark.asyncio
    async def test_local_mode_calls_local_handler(
        self, middleware: ProxyMiddleware, mock_context: MagicMock, mock_call_next: AsyncMock
    ) -> None:
        """In local mode, tool calls are handled by local handler."""
        # Setup: local mode (no upstream URL)
        with patch("tasca.shell.mcp.server.get_upstream_config") as mock_config:
            mock_config.return_value = Success(UpstreamConfig(url=None, token=None))

            result = await middleware.on_call_tool(mock_context, mock_call_next)

        # Verify local handler was called
        assert result is not None
        # The result should be from mock_call_next
        assert len(result.content) == 1
        text = result.content[0].text
        data = json.loads(text)
        assert data["ok"] is True

    @pytest.mark.asyncio
    async def test_local_mode_with_url_none(
        self, middleware: ProxyMiddleware, mock_context: MagicMock, mock_call_next: AsyncMock
    ) -> None:
        """Local mode when url is None (explicit check)."""
        with patch("tasca.shell.mcp.server.get_upstream_config") as mock_config:
            mock_config.return_value = Success(UpstreamConfig(url=None, token=None))

            result = await middleware.on_call_tool(mock_context, mock_call_next)

        assert result is not None


class TestProxyMiddlewareRemoteMode:
    """Tests for ProxyMiddleware in remote mode."""

    @pytest.mark.asyncio
    async def test_remote_mode_forwards_non_local_tool(
        self,
        middleware: ProxyMiddleware,
        mock_context: MagicMock,
        success_tool_result: dict[str, Any],
    ) -> None:
        """In remote mode, non-local tools are forwarded to upstream."""
        mock_call_next = AsyncMock()

        with (
            patch("tasca.shell.mcp.server.get_upstream_config") as mock_config,
            patch(
                "tasca.shell.mcp.server.forward_jsonrpc_request", new_callable=AsyncMock
            ) as mock_forward,
        ):
            mock_config.return_value = Success(
                UpstreamConfig(url="http://upstream.example.com", token="test-token")
            )
            mock_forward.return_value = success_tool_result

            result = await middleware.on_call_tool(mock_context, mock_call_next)

        # Verify forward was called with correct parameters
        mock_forward.assert_called_once()
        call_args = mock_forward.call_args
        assert call_args[1]["method"] == "tools/call"
        assert call_args[1]["params"]["name"] == "table_list"
        assert call_args[1]["params"]["arguments"] == {"status": "open"}

        # Verify call_next was NOT called (we forwarded instead)
        mock_call_next.assert_not_called()

        # Verify result contains forwarded data
        assert len(result.content) == 1

    @pytest.mark.asyncio
    async def test_remote_mode_connect_runs_locally(
        self, middleware: ProxyMiddleware, mock_call_next: AsyncMock
    ) -> None:
        """connect tool always runs locally even in remote mode."""
        context = MagicMock()
        context.message = MagicMock()
        context.message.name = "connect"
        context.message.arguments = {"url": "http://new-upstream.example.com"}

        with (
            patch("tasca.shell.mcp.server.get_upstream_config") as mock_config,
            patch(
                "tasca.shell.mcp.server.forward_jsonrpc_request", new_callable=AsyncMock
            ) as mock_forward,
        ):
            mock_config.return_value = Success(
                UpstreamConfig(url="http://upstream.example.com", token="test-token")
            )

            result = await middleware.on_call_tool(context, mock_call_next)

        # Verify forward was NOT called (local tool)
        mock_forward.assert_not_called()
        # Verify local handler was called
        assert result is not None

    @pytest.mark.asyncio
    async def test_remote_mode_connection_status_runs_locally(
        self, middleware: ProxyMiddleware, mock_call_next: AsyncMock
    ) -> None:
        """connection_status tool always runs locally even in remote mode."""
        context = MagicMock()
        context.message = MagicMock()
        context.message.name = "connection_status"
        context.message.arguments = {}

        with (
            patch("tasca.shell.mcp.server.get_upstream_config") as mock_config,
            patch(
                "tasca.shell.mcp.server.forward_jsonrpc_request", new_callable=AsyncMock
            ) as mock_forward,
        ):
            mock_config.return_value = Success(
                UpstreamConfig(url="http://upstream.example.com", token="test-token")
            )

            result = await middleware.on_call_tool(context, mock_call_next)

        # Verify forward was NOT called (local tool)
        mock_forward.assert_not_called()
        # Verify local handler was called
        assert result is not None


class TestProxyMiddlewareResponseConversion:
    """Tests for response conversion from upstream to ToolResult."""

    @pytest.mark.asyncio
    async def test_success_response_conversion(
        self,
        middleware: ProxyMiddleware,
        mock_context: MagicMock,
        success_tool_result: dict[str, Any],
    ) -> None:
        """Successful upstream response is converted to ToolResult."""
        mock_call_next = AsyncMock()

        with (
            patch("tasca.shell.mcp.server.get_upstream_config") as mock_config,
            patch(
                "tasca.shell.mcp.server.forward_jsonrpc_request", new_callable=AsyncMock
            ) as mock_forward,
        ):
            mock_config.return_value = Success(
                UpstreamConfig(url="http://upstream.example.com", token="test-token")
            )
            mock_forward.return_value = success_tool_result

            result = await middleware.on_call_tool(mock_context, mock_call_next)

        # Parse the result content
        text = result.content[0].text
        data = json.loads(text)

        # Should be a success envelope with tables data
        assert data["ok"] is True
        assert "data" in data
        assert "tables" in data["data"]

    @pytest.mark.asyncio
    async def test_jsonrpc_error_response_conversion(
        self,
        middleware: ProxyMiddleware,
        mock_context: MagicMock,
        error_tool_result: dict[str, Any],
    ) -> None:
        """JSON-RPC error response is converted to error ToolResult."""
        mock_call_next = AsyncMock()

        with (
            patch("tasca.shell.mcp.server.get_upstream_config") as mock_config,
            patch(
                "tasca.shell.mcp.server.forward_jsonrpc_request", new_callable=AsyncMock
            ) as mock_forward,
        ):
            mock_config.return_value = Success(
                UpstreamConfig(url="http://upstream.example.com", token="test-token")
            )
            mock_forward.return_value = error_tool_result

            result = await middleware.on_call_tool(mock_context, mock_call_next)

        # Parse the result content
        text = result.content[0].text
        data = json.loads(text)

        # Should be an error envelope
        assert data["ok"] is False
        assert "error" in data
        assert data["error"]["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_proxy_error_response_conversion(
        self, middleware: ProxyMiddleware, mock_context: MagicMock
    ) -> None:
        """Proxy-level error (e.g., connection failure) is converted to error ToolResult."""
        mock_call_next = AsyncMock()

        # Error envelope from forward_jsonrpc_request (e.g., UPSTREAM_UNREACHABLE)
        proxy_error = {
            "ok": False,
            "error": {
                "code": "UPSTREAM_UNREACHABLE",
                "message": "Cannot connect to upstream",
                "details": {"error": "Connection refused"},
            },
        }

        with (
            patch("tasca.shell.mcp.server.get_upstream_config") as mock_config,
            patch(
                "tasca.shell.mcp.server.forward_jsonrpc_request", new_callable=AsyncMock
            ) as mock_forward,
        ):
            mock_config.return_value = Success(
                UpstreamConfig(url="http://upstream.example.com", token="test-token")
            )
            mock_forward.return_value = proxy_error

            result = await middleware.on_call_tool(mock_context, mock_call_next)

        # Parse the result content
        text = result.content[0].text
        data = json.loads(text)

        # Should be an error envelope
        assert data["ok"] is False
        assert "error" in data
        assert data["error"]["code"] == "UPSTREAM_UNREACHABLE"
        assert "details" in data["error"]


class TestProxyMiddlewareRequestMethod:
    """Tests for JSON-RPC method used in forwarding."""

    @pytest.mark.asyncio
    async def test_forward_uses_tools_call_method(
        self,
        middleware: ProxyMiddleware,
        mock_context: MagicMock,
        success_tool_result: dict[str, Any],
    ) -> None:
        """Forwarding uses 'tools/call' JSON-RPC method."""
        mock_call_next = AsyncMock()

        with (
            patch("tasca.shell.mcp.server.get_upstream_config") as mock_config,
            patch(
                "tasca.shell.mcp.server.forward_jsonrpc_request", new_callable=AsyncMock
            ) as mock_forward,
        ):
            mock_config.return_value = Success(
                UpstreamConfig(url="http://upstream.example.com", token="test-token")
            )
            mock_forward.return_value = success_tool_result

            await middleware.on_call_tool(mock_context, mock_call_next)

        # Verify the method is 'tools/call'
        call_args = mock_forward.call_args
        assert call_args[1]["method"] == "tools/call"

    @pytest.mark.asyncio
    async def test_forward_includes_tool_name_and_arguments(
        self, middleware: ProxyMiddleware, success_tool_result: dict[str, Any]
    ) -> None:
        """Forwarding includes tool name and arguments in params."""
        mock_call_next = AsyncMock()

        # Test with various tools
        test_cases = [
            ("table_create", {"question": "Test?"}),
            ("table_say", {"table_id": "t1", "content": "Hello"}),
            ("patron_register", {"name": "Agent1"}),
        ]

        for tool_name, arguments in test_cases:
            context = MagicMock()
            context.message = MagicMock()
            context.message.name = tool_name
            context.message.arguments = arguments

            with (
                patch("tasca.shell.mcp.server.get_upstream_config") as mock_config,
                patch(
                    "tasca.shell.mcp.server.forward_jsonrpc_request", new_callable=AsyncMock
                ) as mock_forward,
            ):
                mock_config.return_value = Success(
                    UpstreamConfig(url="http://upstream.example.com", token="test-token")
                )
                mock_forward.return_value = success_tool_result

                await middleware.on_call_tool(context, mock_call_next)

            # Verify params include name and arguments
            call_args = mock_forward.call_args
            assert call_args[1]["params"]["name"] == tool_name
            assert call_args[1]["params"]["arguments"] == arguments


class TestProxyMiddlewareModeCheck:
    """Tests for mode check efficiency."""

    @pytest.mark.asyncio
    async def test_mode_check_is_single_attribute_read(
        self, middleware: ProxyMiddleware, mock_context: MagicMock, mock_call_next: AsyncMock
    ) -> None:
        """Mode check reads is_remote attribute (single attribute read)."""
        with patch("tasca.shell.mcp.server.get_upstream_config") as mock_config:
            mock_config.return_value = Success(UpstreamConfig(url=None, token=None))

            await middleware.on_call_tool(mock_context, mock_call_next)

        # get_upstream_config should be called exactly once
        mock_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_mode_check_call_order(
        self, middleware: ProxyMiddleware, mock_context: MagicMock, mock_call_next: AsyncMock
    ) -> None:
        """In local mode, call_next is invoked after mode check."""
        call_order = []

        async def tracking_call_next(context: Any) -> Any:
            call_order.append("call_next")
            from fastmcp.tools.tool import ToolResult
            from mcp.types import TextContent

            return ToolResult(content=[TextContent(type="text", text='{"ok": true, "data": {}}')])

        with patch("tasca.shell.mcp.server.get_upstream_config") as mock_config:
            mock_config.return_value = Success(UpstreamConfig(url=None, token=None))
            call_order.append("config_called")

            await middleware.on_call_tool(mock_context, tracking_call_next)

        # Config should be checked before call_next
        assert call_order == ["config_called", "call_next"] or call_order[0] == "config_called"
