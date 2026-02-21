"""
Integration test fixtures and configuration.

This module provides fixtures for testing both REST API and MCP endpoints.
All base URLs are configurable via environment variables for flexibility.

Environment Variables:
    TASCA_TEST_API_URL: Base URL for REST API (default: http://localhost:8000)
    TASCA_TEST_MCP_URL: Base URL for MCP HTTP endpoint (default: http://localhost:8000/mcp)
    TASCA_TEST_TIMEOUT: Request timeout in seconds (default: 30)

Usage:
    # Run HTTP integration tests with in-process ASGI (no external server needed)
    pytest tests/integration/test_mcp.py -v -k "not stdio"

    # Run MCP STDIO tests (uses tasca-mcp command directly)
    pytest tests/integration/test_mcp.py -v -k stdio

    # Run with custom external URL (requires running server)
    TASCA_TEST_API_URL=http://api.example.com pytest tests/integration/
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Generator

import pytest
import pytest_asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    import httpx
    from fastapi import FastAPI
    from starlette.testclient import TestClient

# =============================================================================
# Configuration
# =============================================================================

# Base URLs - configurable via environment variables
API_BASE_URL = os.environ.get("TASCA_TEST_API_URL", "http://localhost:8000")
# MCP endpoint is at /mcp/mcp (FastMCP mounts at /mcp with internal route /mcp)
MCP_BASE_URL = os.environ.get("TASCA_TEST_MCP_URL", f"{API_BASE_URL}/mcp/mcp")
REQUEST_TIMEOUT = int(os.environ.get("TASCA_TEST_TIMEOUT", "30"))

# Environment variable to force external server (skip ASGI fixture)
USE_EXTERNAL_SERVER = os.environ.get("TASCA_USE_EXTERNAL_SERVER", "").lower() in (
    "1",
    "true",
    "yes",
)


# =============================================================================
# Configuration Fixtures
# =============================================================================


@pytest.fixture
def api_base_url() -> str:
    """Base URL for REST API endpoints.

    Override with TASCA_TEST_API_URL environment variable.

    Returns:
        Base URL string (e.g., "http://localhost:8000")
    """
    return API_BASE_URL


@pytest.fixture
def mcp_base_url() -> str:
    """Base URL for MCP HTTP endpoint.

    Override with TASCA_TEST_MCP_URL environment variable.
    Defaults to {API_BASE_URL}/mcp.

    Returns:
        Base URL string (e.g., "http://localhost:8000/mcp")
    """
    return MCP_BASE_URL


@pytest.fixture
def request_timeout() -> int:
    """Request timeout in seconds.

    Override with TASCA_TEST_TIMEOUT environment variable.

    Returns:
        Timeout in seconds (default: 30)
    """
    return REQUEST_TIMEOUT


# =============================================================================
# ASGI Application Fixtures
# =============================================================================


@pytest.fixture
def asgi_app() -> "FastAPI":
    """Create a FastAPI app instance for ASGI testing.

    This fixture creates the application without starting a server,
    allowing httpx to test it directly via ASGI transport.

    Note: For MCP tests that require lifespan (Streamable HTTP transport),
    use the mcp_test_client fixture instead, which uses Starlette TestClient
    to properly handle FastMCP's task group initialization.

    Returns:
        FastAPI app instance
    """
    from tasca.shell.api.app import create_app

    return create_app()


@pytest.fixture
def mcp_test_client() -> Generator["TestClient", None, None]:
    """Create a test client for MCP HTTP testing with proper lifespan handling.

    Uses Starlette TestClient which properly handles FastMCP's Streamable HTTP
    transport requirements (task group initialization via lifespan events).

    This fixture MUST be used instead of httpx AsyncClient for MCP HTTP tests,
    as httpx ASGI transport does not trigger ASGI lifespan events.

    Yields:
        TestClient configured for MCP endpoint testing
    """
    from starlette.testclient import TestClient

    from tasca.shell.api.app import create_app

    app = create_app()

    with TestClient(
        app,
        base_url="http://test",
        raise_server_exceptions=True,
    ) as client:
        yield client


# =============================================================================
# HTTP Client Fixtures (REST API)
# =============================================================================


@pytest_asyncio.fixture
async def http_client(asgi_app: "FastAPI") -> AsyncGenerator:
    """Async HTTP client for REST API testing.

    Uses httpx ASGI transport for in-process testing without requiring
    an external server. Set TASCA_USE_EXTERNAL_SERVER=1 to use external URL.

    Provides an httpx.AsyncClient configured with:
    - ASGI transport for in-process testing (default)
    - OR external URL if TASCA_USE_EXTERNAL_SERVER is set
    - Automatic resource cleanup

    Yields:
        httpx.AsyncClient instance
    """
    import httpx

    if USE_EXTERNAL_SERVER:
        # Use external server (requires running server)
        async with httpx.AsyncClient(
            base_url=API_BASE_URL,
            timeout=httpx.Timeout(REQUEST_TIMEOUT),
        ) as client:
            yield client
    else:
        # Use ASGI transport for in-process testing
        transport = httpx.ASGITransport(app=asgi_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            timeout=httpx.Timeout(REQUEST_TIMEOUT),
        ) as client:
            yield client


# =============================================================================
# MCP Client Fixtures (HTTP Transport)
# =============================================================================


@pytest_asyncio.fixture
async def mcp_http_client(asgi_app: "FastAPI") -> AsyncGenerator:
    """MCP client fixture - DEPRECATED: Use mcp_test_client instead.

    This fixture is kept for backward compatibility but does not work
    for FastMCP's Streamable HTTP transport. Use mcp_test_client instead.

    Yields:
        httpx.AsyncClient (note: will not work for MCP tests)
    """
    import httpx

    if USE_EXTERNAL_SERVER:
        # Use external server (requires running server)
        async with httpx.AsyncClient(
            base_url=MCP_BASE_URL,
            timeout=httpx.Timeout(REQUEST_TIMEOUT),
        ) as client:
            yield client
    else:
        # Use ASGI transport for in-process testing
        transport = httpx.ASGITransport(app=asgi_app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test/mcp/mcp",
            timeout=httpx.Timeout(REQUEST_TIMEOUT),
        ) as client:
            yield client


# =============================================================================
# MCP Helper Functions
# =============================================================================


class MCPClient:
    """Helper class for MCP JSON-RPC testing.

    This class provides utility methods for constructing and sending
    MCP protocol messages over HTTP transport.
    """

    def __init__(self, http_client: "httpx.AsyncClient") -> None:
        """Initialize MCP client with HTTP client.

        Args:
            http_client: Configured httpx AsyncClient targeting MCP endpoint
        """
        self._client = http_client
        self._request_id = 0

    def _next_id(self) -> int:
        """Get next request ID for JSON-RPC."""
        self._request_id += 1
        return self._request_id

    async def initialize(self) -> dict:
        """Send MCP initialize request.

        Returns:
            Server capabilities and info
        """
        import httpx

        request_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "tasca-test-client",
                    "version": "0.1.0",
                },
            },
        }

        response = await self._client.post("/", json=payload)
        response.raise_for_status()
        return response.json()

    async def list_tools(self) -> dict:
        """Send MCP tools/list request.

        Returns:
            List of available MCP tools
        """
        import httpx

        request_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/list",
            "params": {},
        }

        response = await self._client.post("/", json=payload)
        response.raise_for_status()
        return response.json()

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Send MCP tools/call request.

        Args:
            name: Tool name to call
            arguments: Optional tool arguments

        Returns:
            Tool execution result
        """
        import httpx

        request_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments or {},
            },
        }

        response = await self._client.post("/", json=payload)
        response.raise_for_status()
        return response.json()


@pytest_asyncio.fixture
async def mcp_client(mcp_http_client: "httpx.AsyncClient") -> AsyncGenerator[MCPClient, None]:
    """MCP client helper for HTTP transport.

    Provides an MCPClient instance for easy MCP protocol testing.

    Yields:
        MCPClient instance configured for the MCP endpoint
    """
    yield MCPClient(mcp_http_client)


# =============================================================================
# Server Availability Checks
# =============================================================================


def check_server_available(url: str, timeout: float = 5.0) -> bool:
    """Check if a server is available at the given URL.

    Args:
        url: Server URL to check
        timeout: Connection timeout in seconds

    Returns:
        True if server is available, False otherwise
    """
    import httpx

    try:
        response = httpx.get(f"{url}/api/v1/health", timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


@pytest.fixture
def skip_if_server_unavailable() -> Generator[None, None, None]:
    """Skip test if server is not available.

    This fixture checks if the API server is running and skips the test
    if it's not available. Useful for tests that require a live server.
    """
    import pytest

    if not check_server_available(API_BASE_URL):
        pytest.skip(f"Server not available at {API_BASE_URL}")
    yield


# =============================================================================
# Test Data Fixtures
# =============================================================================


@pytest.fixture
def sample_patron_data() -> dict:
    """Sample patron data for testing.

    Returns:
        Dictionary with patron fields
    """
    return {
        "patron_id": "test-patron-001",
        "display_name": "Test Agent",
        "alias": "testagent",
        "meta": {"test": True},
    }


@pytest.fixture
def sample_table_data() -> dict:
    """Sample table data for testing.

    Returns:
        Dictionary with table creation fields
    """
    return {
        "created_by": "test-patron-001",
        "title": "Test Discussion Table",
        "host_ids": ["test-patron-001"],
        "metadata": {"topic": "testing"},
    }


@pytest.fixture
def sample_saying_data() -> dict:
    """Sample saying data for testing.

    Returns:
        Dictionary with saying creation fields
    """
    return {
        "content": "Hello from integration test!",
        "patron_id": "test-patron-001",
        "speaker_kind": "agent",
        "saying_type": "text",
    }
