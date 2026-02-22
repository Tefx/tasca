"""
Unit tests for MCP proxy module (UpstreamConfig and forward_jsonrpc_request).

Tests the core proxy components that manage upstream configuration and
handle JSON-RPC request forwarding.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tasca.shell.mcp.proxy import (
    UpstreamConfig,
    forward_jsonrpc_request,
    get_upstream_config,
    switch_to_local,
    switch_to_remote,
)


# =============================================================================
# UpstreamConfig Tests
# =============================================================================


class TestUpstreamConfigDefaults:
    """Tests for UpstreamConfig default state and behavior."""

    def test_default_state_is_local(self) -> None:
        """UpstreamConfig default state is local (url=None, token=None)."""
        config = UpstreamConfig()

        assert config.url is None
        assert config.token is None
        assert config.is_remote is False

    def test_default_state_with_token_only_still_local(self) -> None:
        """Token without URL still means local mode."""
        config = UpstreamConfig(url=None, token="unused-token")

        assert config.url is None
        assert config.token == "unused-token"
        assert config.is_remote is False

    def test_default_is_remote_false(self) -> None:
        """is_remote property returns False for default config."""
        config = UpstreamConfig()

        assert config.is_remote is False


class TestUpstreamConfigSwitchToRemote:
    """Tests for UpstreamConfig.switch_to_remote method."""

    def test_switch_to_remote_sets_url_and_token(self) -> None:
        """switch_to_remote sets url, token, and is_remote=True."""
        config = UpstreamConfig()
        config.switch_to_remote("http://api.example.com", "secret-token")

        assert config.url == "http://api.example.com"
        assert config.token == "secret-token"
        assert config.is_remote is True

    def test_switch_to_remote_sets_url_without_token(self) -> None:
        """switch_to_remote works without token."""
        config = UpstreamConfig()
        config.switch_to_remote("http://api.example.com")

        assert config.url == "http://api.example.com"
        assert config.token is None
        assert config.is_remote is True

    def test_switch_to_remote_overwrites_existing(self) -> None:
        """switch_to_remote overwrites existing config."""
        config = UpstreamConfig(url="http://old.example.com", token="old-token")
        config.switch_to_remote("http://new.example.com", "new-token")

        assert config.url == "http://new.example.com"
        assert config.token == "new-token"

    def test_is_remote_true_after_switch(self) -> None:
        """is_remote returns True after switch_to_remote."""
        config = UpstreamConfig()
        assert config.is_remote is False

        config.switch_to_remote("http://api.example.com")

        assert config.is_remote is True


class TestUpstreamConfigSwitchToLocal:
    """Tests for UpstreamConfig.switch_to_local method."""

    def test_switch_to_local_resets_state(self) -> None:
        """switch_to_local resets all state to defaults."""
        config = UpstreamConfig(url="http://api.example.com", token="secret")
        config.switch_to_local()

        assert config.url is None
        assert config.token is None
        assert config.is_remote is False

    def test_switch_to_local_idempotent(self) -> None:
        """switch_to_local is idempotent (safe to call multiple times)."""
        config = UpstreamConfig()

        config.switch_to_local()
        config.switch_to_local()

        assert config.url is None
        assert config.token is None
        assert config.is_remote is False

    def test_switch_to_local_from_remote(self) -> None:
        """switch_to_local from remote mode clears url and token."""
        config = UpstreamConfig()
        config.switch_to_remote("http://api.example.com", "secret")
        assert config.is_remote is True

        config.switch_to_local()

        assert config.is_remote is False
        assert config.url is None
        assert config.token is None


class TestUpstreamConfigSerialization:
    """Tests for UpstreamConfig serialization methods."""

    def test_to_dict_default(self) -> None:
        """to_dict returns correct dict for default config."""
        config = UpstreamConfig()
        result = config.to_dict()

        assert result == {"url": None, "token": None}

    def test_to_dict_with_values(self) -> None:
        """to_dict returns correct dict for configured upstream."""
        config = UpstreamConfig(url="http://api.example.com", token="secret")
        result = config.to_dict()

        assert result == {"url": "http://api.example.com", "token": "secret"}

    def test_from_dict_default(self) -> None:
        """from_dict creates correct config from empty dict."""
        config = UpstreamConfig.from_dict({})

        assert config.url is None
        assert config.token is None

    def test_from_dict_with_values(self) -> None:
        """from_dict creates correct config from populated dict."""
        config = UpstreamConfig.from_dict({"url": "http://api.example.com", "token": "secret"})

        assert config.url == "http://api.example.com"
        assert config.token == "secret"

    def test_roundtrip_serialization(self) -> None:
        """to_dict -> from_dict roundtrip preserves data."""
        original = UpstreamConfig(url="http://api.example.com", token="secret")
        data = original.to_dict()
        restored = UpstreamConfig.from_dict(data)

        assert restored.url == original.url
        assert restored.token == original.token
        assert restored.is_remote == original.is_remote


class TestGlobalConfigFunctions:
    """Tests for module-level config functions."""

    def test_get_upstream_config_returns_singleton(self) -> None:
        """get_upstream_config returns the global singleton."""
        from tasca.shell.mcp import proxy as proxy_module

        # Reset to known state
        proxy_module._config = UpstreamConfig()

        config1 = get_upstream_config()
        config2 = get_upstream_config()

        assert config1 is config2

    def test_switch_to_remote_modifies_global(self) -> None:
        """switch_to_remote modifies the global config."""
        from tasca.shell.mcp import proxy as proxy_module

        try:
            switch_to_remote("http://api.example.com", "secret")

            config = get_upstream_config()
            assert config.url == "http://api.example.com"
            assert config.token == "secret"
            assert config.is_remote is True
        finally:
            switch_to_local()

    def test_switch_to_local_modifies_global(self) -> None:
        """switch_to_local resets the global config."""
        from tasca.shell.mcp import proxy as proxy_module

        try:
            switch_to_remote("http://api.example.com", "secret")
            switch_to_local()

            config = get_upstream_config()
            assert config.url is None
            assert config.token is None
            assert config.is_remote is False
        finally:
            switch_to_local()


# =============================================================================
# forward_jsonrpc_request Tests
# =============================================================================


class TestForwardJsonrpcRequestSuccess:
    """Tests for successful HTTP request forwarding."""

    @pytest.mark.asyncio
    async def test_sends_correct_http_request(self) -> None:
        """forward_jsonrpc_request sends correct HTTP POST request."""
        config = UpstreamConfig(url="http://api.example.com/mcp", token="test-token")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "id": "server-id",
            "result": {"ok": True, "data": {"items": []}},
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await forward_jsonrpc_request(
                config, "tools/call", {"name": "table_list", "arguments": {}}
            )

        # Verify HTTP call
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args

        assert call_args[1]["json"]["jsonrpc"] == "2.0"
        assert call_args[1]["json"]["method"] == "tools/call"
        assert call_args[1]["json"]["params"] == {"name": "table_list", "arguments": {}}
        assert "id" in call_args[1]["json"]

        # Verify headers
        headers = call_args[1]["headers"]
        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == "Bearer test-token"

        # Verify result
        assert result["jsonrpc"] == "2.0"
        assert result["result"]["ok"] is True

    @pytest.mark.asyncio
    async def test_request_without_token(self) -> None:
        """forward_jsonrpc_request works without token."""
        config = UpstreamConfig(url="http://api.example.com/mcp")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"jsonrpc": "2.0", "id": "test", "result": {}}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await forward_jsonrpc_request(config, "tools/list", {})

        # Verify Authorization header is not set
        call_args = mock_client.post.call_args
        headers = call_args[1]["headers"]
        assert "Authorization" not in headers

        assert result["jsonrpc"] == "2.0"


class TestForwardJsonrpcRequestErrors:
    """Tests for HTTP error handling in forward_jsonrpc_request."""

    @pytest.mark.asyncio
    async def test_connect_error_returns_upstream_unreachable(self) -> None:
        """ConnectError is mapped to UPSTREAM_UNREACHABLE error."""
        config = UpstreamConfig(url="http://unreachable.example.com")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_client_class.return_value = mock_client

            result = await forward_jsonrpc_request(config, "tools/call", {})

        assert result["ok"] is False
        assert result["error"]["code"] == "UPSTREAM_UNREACHABLE"
        assert "Cannot connect to upstream" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_timeout_exception_returns_upstream_timeout(self) -> None:
        """TimeoutException is mapped to UPSTREAM_TIMEOUT error."""
        config = UpstreamConfig(url="http://slow.example.com")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("Request timed out"))
            mock_client_class.return_value = mock_client

            result = await forward_jsonrpc_request(config, "tools/call", {})

        assert result["ok"] is False
        assert result["error"]["code"] == "UPSTREAM_TIMEOUT"
        assert "timed out" in result["error"]["message"].lower()
        assert "timeout_seconds" in result["error"]["details"]

    @pytest.mark.asyncio
    async def test_http_401_returns_upstream_auth_failed(self) -> None:
        """HTTP 401 response is mapped to UPSTREAM_AUTH_FAILED error."""
        config = UpstreamConfig(url="http://api.example.com", token="invalid-token")

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await forward_jsonrpc_request(config, "tools/call", {})

        assert result["ok"] is False
        assert result["error"]["code"] == "UPSTREAM_AUTH_FAILED"
        assert "Authentication failed" in result["error"]["message"]
        assert result["error"]["details"]["status_code"] == 401

    @pytest.mark.asyncio
    async def test_http_403_returns_upstream_auth_failed(self) -> None:
        """HTTP 403 response is mapped to UPSTREAM_AUTH_FAILED error."""
        config = UpstreamConfig(url="http://api.example.com", token="forbidden-token")

        mock_response = MagicMock()
        mock_response.status_code = 403

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await forward_jsonrpc_request(config, "tools/call", {})

        assert result["ok"] is False
        assert result["error"]["code"] == "UPSTREAM_AUTH_FAILED"
        assert result["error"]["details"]["status_code"] == 403

    @pytest.mark.asyncio
    async def test_http_500_returns_upstream_error(self) -> None:
        """HTTP 500 response is mapped to UPSTREAM_ERROR."""
        config = UpstreamConfig(url="http://api.example.com")

        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await forward_jsonrpc_request(config, "tools/call", {})

        assert result["ok"] is False
        assert result["error"]["code"] == "UPSTREAM_ERROR"
        assert "500" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_no_url_returns_upstream_unreachable(self) -> None:
        """No URL configured returns UPSTREAM_UNREACHABLE error."""
        config = UpstreamConfig()  # No URL

        result = await forward_jsonrpc_request(config, "tools/call", {})

        assert result["ok"] is False
        assert result["error"]["code"] == "UPSTREAM_UNREACHABLE"
        assert "No upstream URL" in result["error"]["message"]


class TestForwardJsonrpcRequestJsonRpc:
    """Tests for JSON-RPC envelope structure."""

    @pytest.mark.asyncio
    async def test_request_has_valid_jsonrpc_id(self) -> None:
        """Request includes a valid UUID as JSON-RPC id."""
        config = UpstreamConfig(url="http://api.example.com")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"jsonrpc": "2.0", "id": "test", "result": {}}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            await forward_jsonrpc_request(config, "tools/call", {})

        call_args = mock_client.post.call_args
        request_id = call_args[1]["json"]["id"]

        # Should be a valid UUID string
        import uuid

        uuid.UUID(request_id)  # Will raise if invalid

    @pytest.mark.asyncio
    async def test_request_jsonrpc_version(self) -> None:
        """Request uses JSON-RPC 2.0."""
        config = UpstreamConfig(url="http://api.example.com")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"jsonrpc": "2.0", "id": "test", "result": {}}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            await forward_jsonrpc_request(config, "tools/call", {})

        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["jsonrpc"] == "2.0"

    @pytest.mark.asyncio
    async def test_request_timeout_is_set(self) -> None:
        """HTTP client uses 30 second timeout."""
        config = UpstreamConfig(url="http://api.example.com")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"jsonrpc": "2.0", "id": "test", "result": {}}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            await forward_jsonrpc_request(config, "tools/call", {})

        # Verify timeout was passed to AsyncClient
        call_kwargs = mock_client_class.call_args[1]
        assert call_kwargs["timeout"] == 30.0
