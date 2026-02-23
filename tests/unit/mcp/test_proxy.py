"""
Unit tests for MCP proxy module (UpstreamConfig and forward_jsonrpc_request).

Tests the core proxy components that manage upstream configuration and
handle JSON-RPC request forwarding.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from returns.result import Success

from tasca.shell.mcp.proxy import (
    UpstreamConfig,
    forward_jsonrpc_request,
    get_upstream_config,
    switch_to_local,
    switch_to_remote,
)


def _make_echo_post(extra_fields: dict | None = None) -> AsyncMock:
    """Return an AsyncMock for httpx client.post that echoes the request id.

    The JSON-RPC response id must match the request id sent in the POST body.
    This helper builds a mock that captures the posted JSON and mirrors the id
    back in the response, satisfying _validate_jsonrpc_response id-match check.

    Args:
        extra_fields: Additional top-level fields to include in the response
            JSON (e.g. ``{"result": {...}}`` or ``{"error": {...}}``).
            Defaults to ``{"result": {}}`` if not provided.
    """
    if extra_fields is None:
        extra_fields = {"result": {}}

    async def _post(*args: object, **kwargs: object) -> MagicMock:
        body = kwargs.get("json", {})
        request_id = body.get("id", "unknown")
        response_dict = {
            "jsonrpc": "2.0",
            "id": request_id,
            **extra_fields,
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = json.dumps(response_dict)
        mock_response.json.return_value = response_dict
        return mock_response

    return AsyncMock(side_effect=_post)


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
        """to_dict masks token in output for safe logging."""
        config = UpstreamConfig(url="http://api.example.com", token="secret")
        result = config.to_dict()

        assert result == {"url": "http://api.example.com", "token": "***"}

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

    def test_to_dict_masks_token_not_roundtrippable(self) -> None:
        """to_dict masks token so it cannot be used for roundtrip serialization.

        to_dict() is for safe logging only. Use self.token directly when the
        real credential is needed.
        """
        original = UpstreamConfig(url="http://api.example.com", token="secret")
        data = original.to_dict()

        assert data["token"] == "***"
        assert data["url"] == original.url


class TestGlobalConfigFunctions:
    """Tests for module-level config functions."""

    def test_get_upstream_config_returns_singleton(self) -> None:
        """get_upstream_config returns the global singleton."""
        from tasca.shell.mcp import proxy as proxy_module

        # Reset to known state
        proxy_module._config = UpstreamConfig()

        result1 = get_upstream_config()
        result2 = get_upstream_config()

        # Both should be Success with the same config
        assert isinstance(result1, Success)
        assert isinstance(result2, Success)
        config1 = result1.unwrap()
        config2 = result2.unwrap()
        assert config1 is config2

    def test_switch_to_remote_modifies_global(self) -> None:
        """switch_to_remote modifies the global config."""
        from tasca.shell.mcp import proxy as proxy_module

        try:
            switch_to_remote("http://api.example.com", "secret")

            result = get_upstream_config()
            assert isinstance(result, Success)
            config = result.unwrap()
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

            result = get_upstream_config()
            assert isinstance(result, Success)
            config = result.unwrap()
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

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = _make_echo_post({"result": {"ok": True, "data": {"items": []}}})
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

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = _make_echo_post()
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

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = _make_echo_post()
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

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = _make_echo_post()
            mock_client_class.return_value = mock_client

            await forward_jsonrpc_request(config, "tools/call", {})

        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["jsonrpc"] == "2.0"

    @pytest.mark.asyncio
    async def test_request_timeout_is_set(self) -> None:
        """HTTP client uses 30 second timeout."""
        config = UpstreamConfig(url="http://api.example.com")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = _make_echo_post()
            mock_client_class.return_value = mock_client

            await forward_jsonrpc_request(config, "tools/call", {})

        # Verify timeout was passed to AsyncClient
        call_kwargs = mock_client_class.call_args[1]
        assert call_kwargs["timeout"] == 30.0


class TestValidateJsonrpcResponse:
    """Tests for _validate_jsonrpc_response validation function."""

    def test_valid_success_response(self) -> None:
        """Valid success response passes validation."""
        from tasca.shell.mcp.proxy import _validate_jsonrpc_response

        result = _validate_jsonrpc_response(
            {"jsonrpc": "2.0", "id": "test-id", "result": {"data": "ok"}},
            "test-id",
        )
        assert result is None

    def test_valid_error_response(self) -> None:
        """Valid error response passes validation."""
        from tasca.shell.mcp.proxy import _validate_jsonrpc_response

        result = _validate_jsonrpc_response(
            {
                "jsonrpc": "2.0",
                "id": "test-id",
                "error": {"code": -32600, "message": "Invalid Request"},
            },
            "test-id",
        )
        assert result is None

    def test_error_with_data_field(self) -> None:
        """Error response with optional data field passes validation."""
        from tasca.shell.mcp.proxy import _validate_jsonrpc_response

        result = _validate_jsonrpc_response(
            {
                "jsonrpc": "2.0",
                "id": "test-id",
                "error": {
                    "code": -32601,
                    "message": "Method not found",
                    "data": {"method": "unknown"},
                },
            },
            "test-id",
        )
        assert result is None

    def test_non_dict_response_fails(self) -> None:
        """Non-dict response fails validation."""
        from tasca.shell.mcp.proxy import _validate_jsonrpc_response

        result = _validate_jsonrpc_response("not a dict", "test-id")
        assert result is not None
        assert result["field"] == "root"
        assert "dict" in result["reason"]

    def test_missing_jsonrpc_fails(self) -> None:
        """Missing jsonrpc field fails validation."""
        from tasca.shell.mcp.proxy import _validate_jsonrpc_response

        result = _validate_jsonrpc_response({"id": "test-id", "result": {}}, "test-id")
        assert result is not None
        assert result["field"] == "jsonrpc"

    def test_wrong_jsonrpc_version_fails(self) -> None:
        """Wrong jsonrpc version fails validation."""
        from tasca.shell.mcp.proxy import _validate_jsonrpc_response

        result = _validate_jsonrpc_response(
            {"jsonrpc": "1.0", "id": "test-id", "result": {}},
            "test-id",
        )
        assert result is not None
        assert result["field"] == "jsonrpc"
        assert "2.0" in result["reason"]

    def test_missing_id_fails(self) -> None:
        """Missing id field fails validation."""
        from tasca.shell.mcp.proxy import _validate_jsonrpc_response

        result = _validate_jsonrpc_response({"jsonrpc": "2.0", "result": {}}, "test-id")
        assert result is not None
        assert result["field"] == "id"

    def test_id_mismatch_fails(self) -> None:
        """Response id not matching expected id fails validation."""
        from tasca.shell.mcp.proxy import _validate_jsonrpc_response

        result = _validate_jsonrpc_response(
            {"jsonrpc": "2.0", "id": "wrong-id", "result": {}},
            "expected-id",
        )
        assert result is not None
        assert result["field"] == "id"
        assert "does not match" in result["reason"]

    def test_missing_both_result_and_error_fails(self) -> None:
        """Missing both result and error fails validation."""
        from tasca.shell.mcp.proxy import _validate_jsonrpc_response

        result = _validate_jsonrpc_response({"jsonrpc": "2.0", "id": "test-id"}, "test-id")
        assert result is not None
        assert result["field"] == "result/error"

    def test_both_result_and_error_fails(self) -> None:
        """Having both result and error fails validation."""
        from tasca.shell.mcp.proxy import _validate_jsonrpc_response

        result = _validate_jsonrpc_response(
            {
                "jsonrpc": "2.0",
                "id": "test-id",
                "result": {},
                "error": {"code": -1, "message": "err"},
            },
            "test-id",
        )
        assert result is not None
        assert result["field"] == "result/error"
        assert "exactly one" in result["reason"]

    def test_error_not_dict_fails(self) -> None:
        """Error not being a dict fails validation."""
        from tasca.shell.mcp.proxy import _validate_jsonrpc_response

        result = _validate_jsonrpc_response(
            {"jsonrpc": "2.0", "id": "test-id", "error": "string error"},
            "test-id",
        )
        assert result is not None
        assert result["field"] == "error"

    def test_error_missing_code_fails(self) -> None:
        """Error missing code field fails validation."""
        from tasca.shell.mcp.proxy import _validate_jsonrpc_response

        result = _validate_jsonrpc_response(
            {"jsonrpc": "2.0", "id": "test-id", "error": {"message": "error without code"}},
            "test-id",
        )
        assert result is not None
        assert "code" in result["reason"]

    def test_error_missing_message_fails(self) -> None:
        """Error missing message field fails validation."""
        from tasca.shell.mcp.proxy import _validate_jsonrpc_response

        result = _validate_jsonrpc_response(
            {"jsonrpc": "2.0", "id": "test-id", "error": {"code": -1}},
            "test-id",
        )
        assert result is not None
        assert "message" in result["reason"]

    def test_error_code_not_int_fails(self) -> None:
        """Error code not being an integer fails validation."""
        from tasca.shell.mcp.proxy import _validate_jsonrpc_response

        result = _validate_jsonrpc_response(
            {"jsonrpc": "2.0", "id": "test-id", "error": {"code": "-1", "message": "err"}},
            "test-id",
        )
        assert result is not None
        assert result["field"] == "error.code"

    def test_error_message_not_str_fails(self) -> None:
        """Error message not being a string fails validation."""
        from tasca.shell.mcp.proxy import _validate_jsonrpc_response

        result = _validate_jsonrpc_response(
            {"jsonrpc": "2.0", "id": "test-id", "error": {"code": -1, "message": 123}},
            "test-id",
        )
        assert result is not None
        assert result["field"] == "error.message"


class TestForwardJsonrpcRequestInvalidResponse:
    """Tests for invalid upstream response handling in forward_jsonrpc_request."""

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self) -> None:
        """Invalid JSON from upstream returns UPSTREAM_INVALID_RESPONSE error."""
        config = UpstreamConfig(url="http://api.example.com")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = json.JSONDecodeError("Expecting value", "doc", 0)
        mock_response.text = "not valid json"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await forward_jsonrpc_request(config, "tools/call", {})

        assert result["ok"] is False
        assert result["error"]["code"] == "UPSTREAM_INVALID_RESPONSE"
        assert "invalid JSON" in result["error"]["message"]
        assert "raw_preview" in result["error"]["details"]

    @pytest.mark.asyncio
    async def test_non_dict_response_returns_error(self) -> None:
        """Non-dict JSON response returns UPSTREAM_INVALID_RESPONSE error."""
        config = UpstreamConfig(url="http://api.example.com")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = json.dumps(["not", "a", "dict"])
        mock_response.json.return_value = ["not", "a", "dict"]

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await forward_jsonrpc_request(config, "tools/call", {})

        assert result["ok"] is False
        assert result["error"]["code"] == "UPSTREAM_INVALID_RESPONSE"
        assert "JSON-RPC shape" in result["error"]["message"]
        assert result["error"]["details"]["field"] == "root"

    @pytest.mark.asyncio
    async def test_missing_jsonrpc_field_returns_error(self) -> None:
        """Response missing jsonrpc field returns UPSTREAM_INVALID_RESPONSE error."""
        config = UpstreamConfig(url="http://api.example.com")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = json.dumps({"id": "test", "result": {}})
        mock_response.json.return_value = {"id": "test", "result": {}}

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_class.return_value = mock_client

            result = await forward_jsonrpc_request(config, "tools/call", {})

        assert result["ok"] is False
        assert result["error"]["code"] == "UPSTREAM_INVALID_RESPONSE"
        assert result["error"]["details"]["field"] == "jsonrpc"

    @pytest.mark.asyncio
    async def test_missing_result_and_error_returns_error(self) -> None:
        """Response missing both result and error returns UPSTREAM_INVALID_RESPONSE error."""
        config = UpstreamConfig(url="http://api.example.com")

        # Echo the request id so id-match passes, then let result/error absence trigger the error.
        async def _post_missing_payload(*args: object, **kwargs: object) -> MagicMock:
            body = kwargs.get("json", {})
            request_id = body.get("id", "unknown")
            response_dict = {"jsonrpc": "2.0", "id": request_id}
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = json.dumps(response_dict)
            mock_response.json.return_value = response_dict
            return mock_response

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = AsyncMock(side_effect=_post_missing_payload)
            mock_client_class.return_value = mock_client

            result = await forward_jsonrpc_request(config, "tools/call", {})

        assert result["ok"] is False
        assert result["error"]["code"] == "UPSTREAM_INVALID_RESPONSE"
        assert "result/error" in result["error"]["details"]["field"]

    @pytest.mark.asyncio
    async def test_malformed_error_object_returns_error(self) -> None:
        """Response with malformed error object returns UPSTREAM_INVALID_RESPONSE error."""
        config = UpstreamConfig(url="http://api.example.com")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = _make_echo_post(
                {"error": {"code": "not-an-int", "message": "error message"}}
            )
            mock_client_class.return_value = mock_client

            result = await forward_jsonrpc_request(config, "tools/call", {})

        assert result["ok"] is False
        assert result["error"]["code"] == "UPSTREAM_INVALID_RESPONSE"
        assert "error.code" in result["error"]["details"]["field"]

    @pytest.mark.asyncio
    async def test_valid_jsonrpc_response_passes_through(self) -> None:
        """Valid JSON-RPC response passes through validation."""
        config = UpstreamConfig(url="http://api.example.com")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = _make_echo_post({"result": {"ok": True, "data": {"items": []}}})
            mock_client_class.return_value = mock_client

            result = await forward_jsonrpc_request(config, "tools/call", {})

        # Should pass through unchanged
        assert result["jsonrpc"] == "2.0"
        assert result["result"]["ok"] is True

    @pytest.mark.asyncio
    async def test_valid_jsonrpc_error_response_passes_through(self) -> None:
        """Valid JSON-RPC error response passes through validation."""
        config = UpstreamConfig(url="http://api.example.com")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.post = _make_echo_post(
                {"error": {"code": -32601, "message": "Method not found"}}
            )
            mock_client_class.return_value = mock_client

            result = await forward_jsonrpc_request(config, "tools/call", {})

        # Should pass through unchanged
        assert result["jsonrpc"] == "2.0"
        assert result["error"]["code"] == -32601
