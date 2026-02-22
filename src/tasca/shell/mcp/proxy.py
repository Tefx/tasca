"""
Upstream configuration for MCP proxy mode.

This module provides runtime state management for switching between
local mode (default) and remote upstream mode.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from tasca.shell.mcp.responses import error_response

if TYPE_CHECKING:
    pass


@dataclass
class UpstreamConfig:
    """Configuration for upstream MCP server connection.

    The upstream config manages the runtime state for proxy mode,
    allowing switching between local and remote operation.

    Attributes:
        url: The upstream server URL (None for local mode).
        token: Authentication token for upstream server.

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

    def to_dict(self) -> dict[str, str | None]:
        """Export config as dictionary.

        Examples:
            >>> UpstreamConfig().to_dict()
            {'url': None, 'token': None}
            >>> UpstreamConfig(url="http://api.example.com", token="secret").to_dict()
            {'url': 'http://api.example.com', 'token': 'secret'}
        """
        return {"url": self.url, "token": self.token}

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


def _load_config_from_file() -> dict[str, str | None] | None:
    """Load configuration from .tasca/upstream.json if it exists.

    Returns:
        Configuration dict or None if file doesn't exist.
    """
    config_path = Path(".tasca/upstream.json")
    if not config_path.exists():
        return None
    try:
        with open(config_path) as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {"url": data.get("url"), "token": data.get("token")}
    except (json.JSONDecodeError, OSError):
        pass
    return None


# Module-level singleton instance
# Default: local mode (url=None)
_config: UpstreamConfig = UpstreamConfig()

# Load initial config from file if it exists
_initial_data = _load_config_from_file()
if _initial_data:
    _config = UpstreamConfig.from_dict(_initial_data)


def get_upstream_config() -> UpstreamConfig:
    """Get the global upstream configuration singleton.

    Returns:
        The module-level UpstreamConfig instance.

    Examples:
        >>> config = get_upstream_config()
        >>> isinstance(config, UpstreamConfig)
        True
    """
    return _config


def switch_to_remote(url: str, token: str | None = None) -> None:
    """Switch the global config to remote upstream mode.

    Args:
        url: The upstream server URL.
        token: Optional authentication token.

    Examples:
        >>> from tasca.shell.mcp.proxy import get_upstream_config, switch_to_remote, switch_to_local
        >>> switch_to_remote("http://api.example.com", "secret")
        >>> get_upstream_config().is_remote
        True
        >>> switch_to_local()  # Reset for other tests
    """
    _config.switch_to_remote(url, token)


def switch_to_local() -> None:
    """Switch the global config to local mode.

    Examples:
        >>> from tasca.shell.mcp.proxy import get_upstream_config, switch_to_remote, switch_to_local
        >>> switch_to_remote("http://api.example.com")
        >>> switch_to_local()
        >>> get_upstream_config().is_remote
        False
    """
    _config.switch_to_local()


# @invar:allow shell_result: MCP response envelopes return primitives, not Result[T, E]
async def forward_jsonrpc_request(
    config: UpstreamConfig, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Send a JSON-RPC request to the upstream MCP endpoint via httpx.

    Args:
        config: UpstreamConfig containing the target URL and auth token.
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
    }
    if config.token:
        headers["Authorization"] = f"Bearer {config.token}"

    async with httpx.AsyncClient(timeout=30.0) as client:
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

            # Parse and return JSON response
            # Type ignore: httpx.Response.json() returns Any, but MCP endpoints return dict
            return response.json()  # type: ignore[no-any-return]

        except httpx.ConnectError as e:
            return error_response(
                "UPSTREAM_UNREACHABLE",
                f"Cannot connect to upstream at {config.url}",
                {"error": str(e)},
            )
        except httpx.TimeoutException as e:
            return error_response(
                "UPSTREAM_TIMEOUT",
                f"Request to upstream timed out",
                {"timeout_seconds": 30.0, "error": str(e)},
            )
