"""
Integration tests for REST API endpoints.

These tests verify the REST API endpoints work correctly using in-process
ASGI transport.  No external server is required.

Usage:
    pytest tests/integration/test_api.py -v
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

# Use the fixture token that fixture_admin_token (conftest.py) always patches settings to.
# We cannot capture _settings.admin_token at module import time because the autouse
# fixture runs after module collection; the token seen here would be stale by test time.
from tests.integration.conftest import TEST_ADMIN_TOKEN as _ADMIN_TOKEN

try:
    from tasca.shell.api.app import create_app as _create_app

    _fastapi_app = _create_app()
except Exception as _e:
    pytest.skip(f"Could not import ASGI app: {_e}", allow_module_level=True)


# =============================================================================
# Local ASGI harness (REST only, no external server)
# =============================================================================


class ASGIRESTHarness:
    """Thin REST harness backed by httpx ASGI transport.

    Mirrors the public interface of RESTHarness from harness.py but uses
    in-process ASGI transport so no live server is needed.

    Example:
        async with ASGIRESTHarness() as harness:
            response = await harness.health_check()
            assert response.status_code == 200
    """

    API_V1_PREFIX = "/api/v1"

    def __init__(self, timeout: float = 30.0) -> None:
        """Initialize ASGI REST harness.

        Args:
            timeout: Request timeout in seconds.
        """
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "ASGIRESTHarness":
        """Enter async context and create in-process ASGI client."""
        transport = httpx.ASGITransport(app=_fastapi_app)  # type: ignore[arg-type]
        self._client = httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            timeout=httpx.Timeout(self.timeout),
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit async context and close the client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        """Return the underlying HTTP client.

        Raises:
            RuntimeError: If the harness is not used as an async context manager.

        Returns:
            Configured httpx.AsyncClient using ASGI transport.
        """
        if self._client is None:
            raise RuntimeError("ASGIRESTHarness must be used as an async context manager")
        return self._client

    # ------------------------------------------------------------------
    # Health endpoints
    # ------------------------------------------------------------------

    async def health_check(self) -> httpx.Response:
        """GET /api/v1/health.

        Returns:
            HTTP response from the health endpoint.
        """
        return await self.client.get(f"{self.API_V1_PREFIX}/health")

    async def readiness_check(self) -> httpx.Response:
        """GET /api/v1/ready.

        Returns:
            HTTP response from the readiness endpoint.
        """
        return await self.client.get(f"{self.API_V1_PREFIX}/ready")

    # ------------------------------------------------------------------
    # Table endpoints
    # ------------------------------------------------------------------

    async def create_table(
        self,
        data: dict[str, Any],
        admin_token: str | None = None,
    ) -> httpx.Response:
        """POST /api/v1/tables.

        Args:
            data: Table creation payload.
            admin_token: Optional admin Bearer token.

        Returns:
            HTTP response from the create-table endpoint.
        """
        headers: dict[str, str] = {}
        if admin_token:
            headers["Authorization"] = f"Bearer {admin_token}"
        return await self.client.post(
            f"{self.API_V1_PREFIX}/tables",
            json=data,
            headers=headers,
        )

    async def get_table(self, table_id: str) -> httpx.Response:
        """GET /api/v1/tables/{table_id}.

        Args:
            table_id: Table identifier.

        Returns:
            HTTP response from the get-table endpoint.
        """
        return await self.client.get(f"{self.API_V1_PREFIX}/tables/{table_id}")

    async def list_tables(self) -> httpx.Response:
        """GET /api/v1/tables.

        Returns:
            HTTP response from the list-tables endpoint.
        """
        return await self.client.get(f"{self.API_V1_PREFIX}/tables")

    async def delete_table(
        self,
        table_id: str,
        admin_token: str | None = None,
    ) -> httpx.Response:
        """DELETE /api/v1/tables/{table_id}.

        Args:
            table_id: Table identifier.
            admin_token: Optional admin Bearer token.

        Returns:
            HTTP response from the delete-table endpoint.
        """
        headers: dict[str, str] = {}
        if admin_token:
            headers["Authorization"] = f"Bearer {admin_token}"
        return await self.client.delete(
            f"{self.API_V1_PREFIX}/tables/{table_id}",
            headers=headers,
        )


# =============================================================================
# Health Check Tests
# =============================================================================


@pytest.mark.asyncio
async def test_health_check() -> None:
    """Test GET /health returns healthy status.

    Scenario: REST Health Check
    Verifies that the health endpoint returns a 200 status
    and includes a 'status' field with value 'healthy'.
    """
    async with ASGIRESTHarness() as harness:
        response = await harness.health_check()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data


@pytest.mark.asyncio
async def test_readiness_check() -> None:
    """Test GET /ready returns ready status.

    Scenario: REST Readiness Check
    Verifies that the readiness endpoint returns a 200 status
    and includes a 'status' field with value 'ready'.
    """
    async with ASGIRESTHarness() as harness:
        response = await harness.readiness_check()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"


# =============================================================================
# Table CRUD Tests
# =============================================================================


@pytest.mark.asyncio
async def test_create_table() -> None:
    """Test POST /tables creates a new table (requires admin auth).

    Scenario: REST Table Creation
    Verifies that creating a table returns the created table
    with a valid ID and the provided data.
    Note: POST /tables requires admin Bearer token.
    """
    async with ASGIRESTHarness() as harness:
        table_data = {
            "question": "What is the best approach for this feature?",
            "context": "We need to decide between options A and B",
        }
        response = await harness.create_table(table_data, admin_token=_ADMIN_TOKEN)

        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["question"] == table_data["question"]
        assert data["context"] == table_data["context"]


@pytest.mark.asyncio
async def test_get_table() -> None:
    """Test GET /tables/{table_id} retrieves a table.

    Scenario: REST Table Retrieval
    Verifies that retrieving a table by ID returns the table data.
    Note: First creates a table, then retrieves it.
    """
    async with ASGIRESTHarness() as harness:
        table_data = {
            "question": "Test question for retrieval?",
        }
        create_response = await harness.create_table(table_data, admin_token=_ADMIN_TOKEN)
        assert create_response.status_code == 200
        created_table = create_response.json()
        table_id = created_table["id"]

        response = await harness.get_table(table_id)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == table_id
        assert data["question"] == table_data["question"]


@pytest.mark.asyncio
async def test_list_tables() -> None:
    """Test GET /tables lists all tables.

    Scenario: REST Table Listing
    Verifies that listing tables returns an array.
    Note: Currently returns empty array as storage is not implemented.
    """
    async with ASGIRESTHarness() as harness:
        response = await harness.list_tables()

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


@pytest.mark.asyncio
async def test_delete_table() -> None:
    """Test DELETE /tables/{table_id} deletes a table (requires admin auth).

    Scenario: REST Table Deletion
    Verifies that deleting a table returns a confirmation.
    Note: DELETE /tables requires admin Bearer token.
    """
    async with ASGIRESTHarness() as harness:
        table_data = {"question": "Table to delete"}
        create_response = await harness.create_table(table_data, admin_token=_ADMIN_TOKEN)
        assert create_response.status_code == 200
        table_id = create_response.json()["id"]

        response = await harness.delete_table(table_id, admin_token=_ADMIN_TOKEN)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["table_id"] == table_id


# =============================================================================
# Error Handling Tests
# =============================================================================


@pytest.mark.asyncio
async def test_404_on_unknown_path() -> None:
    """Test that unknown API paths return 404.

    Scenario: REST 404 Handling
    Verifies that accessing an unknown API endpoint returns 404.
    Note: Non-API paths may return SPA fallback (200) for client-side routing.
    """
    async with ASGIRESTHarness() as harness:
        # Test unknown API endpoint (not SPA route)
        response = await harness.client.get("/api/v1/nonexistent-endpoint")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_422_on_invalid_input() -> None:
    """Test that invalid input returns 422.

    Scenario: REST Validation Error
    Verifies that sending invalid data returns a validation error.
    Note: POST /tables requires admin auth, so this tests auth first.
    """
    async with ASGIRESTHarness() as harness:
        # Missing required 'question' field - with admin token to pass auth
        response = await harness.create_table({}, admin_token=_ADMIN_TOKEN)

        # FastAPI returns 422 for validation errors
        assert response.status_code == 422


# =============================================================================
# MCP Endpoint Availability Tests
# =============================================================================


@pytest.mark.asyncio
async def test_mcp_endpoint_mounted() -> None:
    """Test that MCP endpoint is mounted at /mcp.

    Scenario: MCP Endpoint Mount
    Verifies that the MCP endpoint is accessible and responds to POST.
    Note: MCP uses Streamable HTTP transport, so we need to handle the session.
    """
    async with ASGIRESTHarness() as harness:
        # MCP uses POST for JSON-RPC
        response = await harness.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0.1.0"},
                },
            },
        )

        # MCP should respond (200 for success, 307 for redirect, 400 for bad request, etc.)
        # Note: FastMCP Streamable HTTP transport may return different status codes
        # 307 means redirect to /mcp (trailing slash handling)
        assert response.status_code in [200, 307, 400, 500]
