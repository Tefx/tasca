"""
Upstream configuration for MCP proxy mode.

This module provides runtime state management for switching between
local mode (default) and remote upstream mode.

MCP HTTP Transport Session Management:
    MCP HTTP transport requires session management via the 'mcp-session-id' header:
    1. Client sends 'initialize' request to upstream
    2. Server responds with 'mcp-session-id' header
    3. Client must include this header on all subsequent requests (tools/call, etc.)

    The proxy mode handles this automatically:
    - When switching to remote mode, it initializes an MCP session with upstream
    - The session_id is stored in UpstreamConfig
    - All forwarded requests include the mcp-session-id header

Escape Hatch Convention (shell_result):
    MCP proxy helpers return primitive dicts or use MCP protocol patterns.
    See server.py module docstring for MCP protocol rationale.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from returns.result import Failure, Result, Success

from tasca.core.mcp_jsonrpc import (
    parse_sse_or_json as _parse_sse_or_json,
    validate_jsonrpc_response as _validate_jsonrpc_response,
)
from tasca.shell.mcp.responses import error_response

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# MCP protocol version for session initialization
MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_CLIENT_NAME = "tasca-proxy"
MCP_CLIENT_VERSION = "0.1.0"


@dataclass
class UpstreamConfig:
    """Configuration for upstream MCP server connection.

    The upstream config manages the runtime state for proxy mode,
    allowing switching between local and remote operation.

    Attributes:
        url: The upstream server URL (None for local mode).
        token: Authentication token for upstream server.
        session_id: MCP session ID for the upstream connection (required for HTTP transport).

    Examples:
        >>> config = UpstreamConfig()
        >>> config.url is None
        True
        >>> config.token is None
        True
        >>> config.is_remote
        False

        >>> config = UpstreamConfig(url="http://localhost:8080", token="secret")
        >>> config.is_remote
        True

        >>> config = UpstreamConfig(url="http://localhost:8080")
        >>> config.is_remote
        True

        >>> config = UpstreamConfig(url=None, token="unused")
        >>> config.is_remote
        False
    """

    url: str | None = None
    token: str | None = None
    session_id: str | None = None

    @property
    def is_remote(self) -> bool:
        """Check if configured for remote upstream mode.

        Returns:
            True if url is set, False otherwise (local mode).

        Examples:
            >>> UpstreamConfig().is_remote
            False
            >>> UpstreamConfig(url="http://api.example.com").is_remote
            True
        """
        return self.url is not None

    def switch_to_remote(self, url: str, token: str | None = None) -> None:
        """Switch to remote upstream mode.

        Args:
            url: The upstream server URL.
            token: Optional authentication token.

        Examples:
            >>> config = UpstreamConfig()
            >>> config.switch_to_remote("http://api.example.com", "secret")
            >>> config.url
            'http://api.example.com'
            >>> config.token
            'secret'
            >>> config.is_remote
            True
        """
        self.url = url
        self.token = token
        self.session_id = None  # Reset session on config change

    def switch_to_local(self) -> None:
        """Switch to local mode (reset to defaults).

        Examples:
            >>> config = UpstreamConfig(url="http://api.example.com", token="secret")
            >>> config.switch_to_local()
            >>> config.url is None
            True
            >>> config.token is None
            True
            >>> config.is_remote
            False
        """
        self.url = None
        self.token = None

    # Display serialization: shows the token field with a redacted sentinel value
    # ("***") so the key is always present in output. Use for human-readable
    # status messages and debug UI where the field structure must be consistent.
    # Prefer safe_dict() for machine-readable logging (e.g. structured JSON logs).
    def to_dict(self) -> dict[str, str | None]:
        """Serialize config for human-readable display, with token redacted as "***".

        Audience: Display and debug output (status messages, CLI output, debug UI).
        The token key is always present; its value is "***" when a token is set,
        or None when no token is configured.

        Prefer safe_dict() when writing to structured logs or passing config data
        to downstream consumers that check token presence programmatically.

        Examples:
            >>> UpstreamConfig().to_dict()
            {'url': None, 'token': None}
            >>> UpstreamConfig(url="http://api.example.com", token="secret").to_dict()
            {'url': 'http://api.example.com', 'token': '***'}
        """
        return {"url": self.url, "token": "***" if self.token else None}

    # Logging serialization: replaces the token field with a boolean presence flag
    # so structured log consumers can check authentication status without any
    # risk of token leakage via log aggregators. Use for machine-readable output.
    # Prefer to_dict() when displaying config to a human operator.
    def safe_dict(self) -> dict[str, str | None | bool]:
        """Serialize config for structured logging, replacing token with a boolean flag.

        Audience: Machine-readable structured logs (e.g. JSON log streams, metrics).
        The token is never included; has_token records whether authentication is
        configured, so downstream consumers can check token presence safely.

        Prefer to_dict() when the output is read by a human operator and the
        field structure (always showing a "token" key) is more important than
        strict absence of the value.

        Examples:
            >>> UpstreamConfig().safe_dict()
            {'url': None, 'has_token': False}
            >>> UpstreamConfig(url="http://api.example.com").safe_dict()
            {'url': 'http://api.example.com', 'has_token': False}
            >>> UpstreamConfig(url="http://api.example.com", token="secret").safe_dict()
            {'url': 'http://api.example.com', 'has_token': True}
        """
        return {"url": self.url, "has_token": self.token is not None}

    @classmethod
    def from_dict(cls, data: dict[str, str | None]) -> UpstreamConfig:
        """Create config from dictionary.

        Args:
            data: Dictionary with 'url' and 'token' keys.

        Examples:
            >>> config = UpstreamConfig.from_dict({"url": "http://api.example.com", "token": "secret"})
            >>> config.url
            'http://api.example.com'
            >>> config.token
            'secret'
        """
        return cls(url=data.get("url"), token=data.get("token"))


class ProxyConfigError(Exception):
    """Error loading proxy configuration."""

    def __init__(self, message: str, path: str = ".tasca/upstream.json") -> None:
        self.path = path
        super().__init__(f"{message}: {path}")


class SessionInitError(Exception):
    """Error initializing MCP session with upstream server."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.details = details or {}
        super().__init__(message)


# @shell_complexity: Multiple branches for HTTP status codes, JSON parse errors, network failures
async def initialize_upstream_session(
    url: str, token: str | None = None
) -> Result[str, SessionInitError]:
    """Initialize an MCP session with the upstream server.

    Sends an 'initialize' request to the upstream MCP server and extracts
    the session ID from the response headers.

    Args:
        url: The upstream MCP server URL.
        token: Optional authentication token for upstream server.

    Returns:
        Success with the session ID string, or Failure with SessionInitError.

    Examples:
        >>> # async test - actual network call
        >>> # result = await initialize_upstream_session("http://localhost:8000/mcp", "secret")
        >>> # isinstance(result, Success)  # True if server is running
    """
    request_id = str(uuid.uuid4())
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {
                "name": MCP_CLIENT_NAME,
                "version": MCP_CLIENT_VERSION,
            },
        },
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)

            # Check for authentication failures
            if response.status_code in (401, 403):
                return Failure(
                    SessionInitError(
                        f"Authentication failed with status {response.status_code}",
                        {"status_code": response.status_code},
                    )
                )

            # Check for other HTTP errors
            if response.status_code >= 400:
                return Failure(
                    SessionInitError(
                        f"Upstream returned status {response.status_code}",
                        {"status_code": response.status_code},
                    )
                )

            # Extract session ID from response headers
            session_id = response.headers.get("mcp-session-id")
            if not session_id:
                # Session ID might be optional for some transports, log warning
                logger.warning(
                    "upstream_session_id_missing",
                    extra={"url": url, "status_code": response.status_code},
                )
                # Return empty string to indicate no session ID (some transports don't use it)
                return Success("")

            logger.info(
                "upstream_session_initialized",
                extra={"url": url, "session_id": session_id[:8] + "..."},
            )
            return Success(session_id)

        except httpx.ConnectError as e:
            return Failure(
                SessionInitError(
                    f"Cannot connect to upstream at {url}",
                    {"error": str(e), "error_type": "connect"},
                )
            )
        except httpx.TimeoutException as e:
            return Failure(
                SessionInitError(
                    f"Timeout connecting to upstream at {url}",
                    {"error": str(e), "error_type": "timeout"},
                )
            )
        except Exception as e:
            return Failure(
                SessionInitError(
                    f"Unexpected error initializing session: {e}",
                    {"error": str(e), "error_type": type(e).__name__},
                )
            )


# Module-level singleton instance
# Default: local mode (url=None)
_config: UpstreamConfig = UpstreamConfig()


def get_upstream_config() -> Result[UpstreamConfig, ProxyConfigError]:
    """Get the global upstream configuration singleton.

    This is a Shell function that retrieves configuration state.
    Returns Result to allow callers to handle config loading errors.

    Returns:
        Success with the UpstreamConfig instance.
        Failure with ProxyConfigError if config could not be loaded.

    Examples:
        >>> result = get_upstream_config()
        >>> isinstance(result, Success)
        True
        >>> config = result.unwrap()
        >>> isinstance(config, UpstreamConfig)
        True
    """
    return Success(_config)


# @invar:allow shell_result: MCP protocol
# @shell_orchestration: Switches module-level runtime state shared by MCP route handlers
def switch_to_remote(url: str, token: str | None = None) -> None:
    """Switch the global config to remote upstream mode.

    Args:
        url: The upstream server URL.
        token: Optional authentication token.

    Examples:
        >>> from tasca.shell.mcp.proxy import get_upstream_config, switch_to_remote, switch_to_local
        >>> switch_to_remote("http://api.example.com", "secret")
        >>> get_upstream_config().unwrap().is_remote
        True
        >>> switch_to_local()  # Reset for other tests
    """
    _config.switch_to_remote(url, token)


# @shell_orchestration: Switches module-level runtime state shared by MCP route handlers
def switch_to_local() -> None:
    """Switch the global config to local mode.

    Examples:
        >>> from tasca.shell.mcp.proxy import get_upstream_config, switch_to_remote, switch_to_local
        >>> switch_to_remote("http://api.example.com")
        >>> switch_to_local()
        >>> get_upstream_config().unwrap().is_remote
        False
    """
    _config.switch_to_local()


async def switch_to_remote_with_session(
    url: str, token: str | None = None
) -> Result[UpstreamConfig, SessionInitError]:
    """Switch to remote mode and initialize MCP session with upstream.

    This is the preferred way to switch to remote mode for MCP HTTP transport,
    as it properly initializes the session with the upstream server.

    Args:
        url: The upstream server URL.
        token: Optional authentication token for upstream server.

    Returns:
        Success with the configured UpstreamConfig (including session_id).
        Failure with SessionInitError if session initialization fails.

    Examples:
        >>> # async example - requires actual server
        >>> # result = await switch_to_remote_with_session("http://localhost:8000/mcp")
        >>> # if isinstance(result, Success):
        >>> #     config = result.unwrap()
        >>> #     config.session_id  # MCP session ID from upstream
    """
    # Initialize MCP session with upstream
    session_result = await initialize_upstream_session(url, token)
    if isinstance(session_result, Failure):
        return session_result

    session_id = session_result.unwrap()

    # Update config with URL, token, and session ID
    # Use the encapsulated method to set url+token (which also resets session_id),
    # then overlay the actual session_id from the upstream handshake.
    global _config
    _config.switch_to_remote(url, token)
    _config.session_id = session_id if session_id else None

    logger.info(
        "switched_to_remote",
        extra={"url": url, "has_session": bool(session_id)},
    )

    return Success(_config)


# @shell_complexity: Upstream proxying requires distinct branches for auth, HTTP status mapping, and network failures
# @invar:allow shell_result: MCP protocol
async def forward_jsonrpc_request(
    config: UpstreamConfig, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Send a JSON-RPC request to the upstream MCP endpoint via httpx.

    Args:
        config: UpstreamConfig containing the target URL, auth token, and session ID.
        method: JSON-RPC method name to call.
        params: Parameters for the JSON-RPC method.

    Returns:
        Parsed JSON response dict on success, or error_response envelope on failure.

    Examples:
        >>> # Success case (requires actual upstream)
        >>> # result = await forward_jsonrpc_request(config, "tools/list", {})
        >>> # result["ok"]  # True on success
    """
    if not config.url:
        return error_response("UPSTREAM_UNREACHABLE", "No upstream URL configured")

    # Build JSON-RPC request envelope
    request_id = str(uuid.uuid4())
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if config.token:
        headers["Authorization"] = f"Bearer {config.token}"

    # Include MCP session ID header if we have a session
    # MCP HTTP transport requires this for all requests after initialization
    if config.session_id:
        headers["mcp-session-id"] = config.session_id

    # timeout: 30s default; for table_wait (10s internal cap), upstream may respond in ~10s
    # Consider per-tool timeout in future versions
    # follow_redirects: True to handle /mcp -> /mcp/ redirects
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            response = await client.post(config.url, json=payload, headers=headers)

            # Check for authentication failures
            if response.status_code in (401, 403):
                return error_response(
                    "UPSTREAM_AUTH_FAILED",
                    f"Authentication failed with status {response.status_code}",
                    {"status_code": response.status_code},
                )

            # Check for other HTTP errors
            if response.status_code >= 400:
                return error_response(
                    "UPSTREAM_ERROR",
                    f"Upstream returned status {response.status_code}",
                    {"status_code": response.status_code},
                )

            # Parse response (may be SSE or plain JSON from FastMCP)
            try:
                data = _parse_sse_or_json(response.text)
            except (json.JSONDecodeError, Exception) as e:
                # Catches both json.JSONDecodeError and deal.PreContractError
                # (which wraps contract violations from @deal.pre decorators)
                return error_response(
                    "UPSTREAM_INVALID_RESPONSE",
                    "Upstream returned invalid JSON",
                    {
                        "error": str(e),
                        "raw_preview": response.text[:200] if response.text else None,
                    },
                )

            # Validate JSON-RPC 2.0 response shape
            validation_error = _validate_jsonrpc_response(data, request_id)
            if validation_error is not None:
                return error_response(
                    "UPSTREAM_INVALID_RESPONSE",
                    f"Upstream response has invalid JSON-RPC shape: {validation_error['reason']}",
                    {"field": validation_error["field"], "response_preview": str(data)[:200]},
                )

            # Type ignore: Validated as dict above, MCP endpoints return dict
            return data  # type: ignore[no-any-return]

        except httpx.ConnectError as e:
            return error_response(
                "UPSTREAM_UNREACHABLE",
                f"Cannot connect to upstream at {config.url}",
                {"error": str(e)},
            )
        except httpx.TimeoutException as e:
            return error_response(
                "UPSTREAM_TIMEOUT",
                "Request to upstream timed out",
                {"timeout_seconds": 30.0, "error": str(e)},
            )
