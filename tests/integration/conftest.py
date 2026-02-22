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
from collections.abc import Generator
from typing import TYPE_CHECKING, TypedDict

import pytest
import pytest_asyncio

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import FastAPI
    from starlette.testclient import TestClient

# Known test token injected via fixture — never changes per test run.
# This value is used in auth tests to guarantee auth is always enforced.
TEST_ADMIN_TOKEN = "test-admin-token-fixture"

# =============================================================================
# Configuration
# =============================================================================

# Base URLs - configurable via environment variables
API_BASE_URL = os.environ.get("TASCA_TEST_API_URL", "http://localhost:8000")
# MCP endpoint is at /mcp
MCP_BASE_URL = os.environ.get("TASCA_TEST_MCP_URL", f"{API_BASE_URL}/mcp")
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


@pytest.fixture(autouse=True)
def fixture_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force a known admin_token value for every integration test.

    This fixture is autouse so it applies to every test in this package
    without requiring explicit use. It patches the module-level settings
    singleton so that both the app middleware and test code see the same
    known token value.

    Scope: function (default) — resets after every test, no leakage.
    """
    import tasca.config as config_module

    monkeypatch.setattr(config_module.settings, "admin_token", TEST_ADMIN_TOKEN)


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
            base_url="http://test/mcp",
            timeout=httpx.Timeout(REQUEST_TIMEOUT),
        ) as client:
            yield client


# =============================================================================
# MCP Session Fixture
# =============================================================================


class MCPSession(TypedDict):
    """Initialized MCP session state for use in tests.

    Attributes:
        client: Starlette TestClient bound to the app under test.
        headers: HTTP headers including Accept and mcp-session-id (if present).
        session_id: MCP session ID returned by the server, or None if absent.
    """

    client: "TestClient"
    headers: dict[str, str]
    session_id: str | None


@pytest.fixture
def mcp_session(mcp_test_client: "TestClient") -> Generator[MCPSession, None, None]:
    """Provide an initialized MCP session for HTTP transport tests.

    Sends the MCP initialize request and extracts the session ID so that
    individual test functions do not need to repeat the boilerplate.

    Includes Bearer token authentication if admin_token is configured.

    Args:
        mcp_test_client: Starlette TestClient with proper lifespan handling.

    Yields:
        MCPSession containing the client, initialized headers, and session_id.
    """
    # Build headers with auth — admin_token is always set via fixture_admin_token.
    headers: dict[str, str] = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {TEST_ADMIN_TOKEN}",
    }

    init_response = mcp_test_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "tasca-test-client",
                    "version": "0.1.0",
                },
            },
        },
        headers=headers,
    )
    assert init_response.status_code == 200

    session_id: str | None = init_response.headers.get("mcp-session-id")
    if session_id:
        headers["mcp-session-id"] = session_id

    yield MCPSession(client=mcp_test_client, headers=headers, session_id=session_id)


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
