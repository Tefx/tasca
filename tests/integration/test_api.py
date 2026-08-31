"""
Integration tests for REST API endpoints.

These tests verify the REST API endpoints work correctly using in-process
ASGI transport.  No external server is required.

Usage:
    pytest tests/integration/test_api.py -v
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

# Use the fixture token that fixture_admin_token (conftest.py) always patches settings to.
# We cannot capture _settings.admin_token at module import time because the autouse
# fixture runs after module collection; the token seen here would be stale by test time.
from tests.integration.conftest import TEST_ADMIN_TOKEN as _ADMIN_TOKEN

_VIEWER_TOKEN = "test-viewer-token-fixture"

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

    async def __aenter__(self) -> ASGIRESTHarness:
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
# Viewer Authentication Tests
# =============================================================================

# Every REST resource endpoint mounted by create_app. The final flag records
# handlers that retain their existing verify_admin_token requirement.
_RESOURCE_ROUTE_MATRIX: tuple[tuple[str, str, str, dict[str, Any], bool], ...] = (
    ("patron register", "POST", "/api/v1/patrons", {"json": {"name": "Viewer test"}}, False),
    ("patron get", "GET", "/api/v1/patrons/missing", {}, False),
    ("table create", "POST", "/api/v1/tables", {"json": {"question": "Viewer test"}}, True),
    ("table list", "GET", "/api/v1/tables", {}, False),
    ("table join", "POST", "/api/v1/tables/join", {"json": {}}, False),
    ("table get", "GET", "/api/v1/tables/missing", {}, False),
    ("table update", "PUT", "/api/v1/tables/missing?expected_version=1", {"json": {"question": "x", "context": None, "status": "open"}}, True),
    ("table delete", "DELETE", "/api/v1/tables/missing", {}, True),
    ("table batch delete", "POST", "/api/v1/tables/actions/batch-delete", {"json": {"ids": ["missing"]}}, True),
    ("table control", "POST", "/api/v1/tables/missing/control", {"json": {"action": "close"}}, True),
    ("saying append", "POST", "/api/v1/tables/missing/sayings", {"json": {"speaker_name": "Admin", "content": "x"}}, True),
    ("saying list", "GET", "/api/v1/tables/missing/sayings", {}, False),
    ("saying wait", "GET", "/api/v1/tables/missing/sayings/wait?since_sequence=-1&timeout=0", {}, False),
    ("seat heartbeat", "POST", "/api/v1/tables/missing/seats/missing/heartbeat", {}, False),
    ("seat list", "GET", "/api/v1/tables/missing/seats", {}, False),
    ("search", "GET", "/api/v1/search?q=viewer", {}, False),
    ("export jsonl", "GET", "/api/v1/tables/missing/export/jsonl", {}, False),
    ("export markdown", "GET", "/api/v1/tables/missing/export/markdown", {}, False),
)


def _bearer_headers(token: str | None) -> dict[str, str]:
    """Build an Authorization header for a matrix credential."""
    return {} if token is None else {"Authorization": f"Bearer {token}"}


async def _resource_request(
    client: httpx.AsyncClient,
    route: tuple[str, str, str, dict[str, Any], bool],
    token: str | None,
) -> httpx.Response:
    """Issue a configured matrix request with one credential."""
    _name, method, path, request_kwargs, _admin_only = route
    return await client.request(method, path, headers=_bearer_headers(token), **request_kwargs)


def _assert_permission_denied(response: httpx.Response) -> None:
    """Assert the standard token failure envelope shared by REST dependencies."""
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "PermissionDenied"
    assert error["details"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("route", _RESOURCE_ROUTE_MATRIX, ids=[route[0] for route in _RESOURCE_ROUTE_MATRIX])
async def test_viewer_auth_matrix_covers_every_rest_resource_router(
    monkeypatch: pytest.MonkeyPatch,
    route: tuple[str, str, str, dict[str, Any], bool],
) -> None:
    """Configured viewer auth gates all resource routers and preserves admin mutations."""
    from tasca.config import settings
    from tasca.shell.api.app import create_app

    monkeypatch.setattr(settings, "viewer_token", _VIEWER_TOKEN)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await _resource_request(client, route, None)
        invalid = await _resource_request(client, route, "invalid-viewer-token")
        viewer = await _resource_request(client, route, _VIEWER_TOKEN)
        admin = await _resource_request(client, route, _ADMIN_TOKEN)

    _assert_permission_denied(missing)
    _assert_permission_denied(invalid)
    if route[4]:
        _assert_permission_denied(viewer)
    else:
        assert viewer.status_code != 401
    assert admin.status_code != 401


@pytest.mark.asyncio
async def test_disabled_viewer_auth_preserves_resource_route_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only existing admin-only mutations reject an anonymous baseline request."""
    from tasca.config import settings
    from tasca.shell.api.app import create_app

    monkeypatch.setattr(settings, "viewer_token", None)
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["viewer_auth_required"] is False

        for route in _RESOURCE_ROUTE_MATRIX:
            response = await _resource_request(client, route, None)
            if route[4]:
                _assert_permission_denied(response)
            else:
                assert response.status_code != 401, route[0]


@pytest.mark.asyncio
async def test_auth_validation_reports_enabled_and_public_viewer_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The auth probe validates roles without mutating service state."""
    from tasca.config import settings
    from tasca.shell.api.app import create_app

    monkeypatch.setattr(settings, "viewer_token", _VIEWER_TOKEN)
    enabled_transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=enabled_transport, base_url="http://test") as client:
        _assert_permission_denied(await client.get("/api/v1/auth/validate"))
        _assert_permission_denied(
            await client.get("/api/v1/auth/validate", headers=_bearer_headers("invalid-viewer-token"))
        )
        assert (await client.get("/api/v1/auth/validate", headers=_bearer_headers(_VIEWER_TOKEN))).json() == {"role": "viewer"}
        assert (await client.get("/api/v1/auth/validate", headers=_bearer_headers(_ADMIN_TOKEN))).json() == {"role": "admin"}

    monkeypatch.setattr(settings, "viewer_token", None)
    disabled_transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=disabled_transport, base_url="http://test") as client:
        assert (await client.get("/api/v1/auth/validate")).json() == {"role": "viewer"}
        assert (await client.get("/api/v1/auth/validate", headers=_bearer_headers(_ADMIN_TOKEN))).json() == {"role": "admin"}
        _assert_permission_denied(
            await client.get("/api/v1/auth/validate", headers=_bearer_headers(_VIEWER_TOKEN))
        )
        _assert_permission_denied(
            await client.get("/api/v1/auth/validate", headers=_bearer_headers("invalid-viewer-token"))
        )


@pytest.mark.asyncio
async def test_public_routes_and_static_shell_stay_public_without_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Utility routes and the SPA shell stay public when viewer auth is configured."""
    import tasca.shell.api.app as app_module
    from tasca.config import settings

    monkeypatch.setattr(settings, "viewer_token", _VIEWER_TOKEN)
    source_path = tmp_path / "src" / "tasca" / "shell" / "api" / "app.py"
    source_path.parent.mkdir(parents=True)
    source_path.touch()
    web_dist = tmp_path / "src" / "tasca" / "web" / "dist"
    (web_dist / "assets").mkdir(parents=True)
    (web_dist / "index.html").write_text("<html>viewer shell</html>", encoding="utf-8")
    (web_dist / "assets" / "app.css").write_text("body {}", encoding="utf-8")
    monkeypatch.setattr(app_module, "Path", lambda _value: source_path)

    transport = httpx.ASGITransport(app=app_module.create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/api/v1/health")
        ready = await client.get("/api/v1/ready")
        docs = await client.get("/docs")
        openapi = await client.get("/openapi.json")
        shell = await client.get("/")
        shell_route = await client.get("/tables/any-table")
        asset = await client.get("/assets/app.css")

    assert health.status_code == 200
    assert health.json()["viewer_auth_required"] is True
    assert ready.status_code == docs.status_code == openapi.status_code == 200
    assert shell.status_code == shell_route.status_code == asset.status_code == 200
    assert "viewer shell" in shell.text and "viewer shell" in shell_route.text
    assert _VIEWER_TOKEN not in openapi.text


def test_viewer_auth_logging_contains_only_state(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """App startup records viewer-auth state without credential values."""
    from tasca.config import settings
    from tasca.shell.api.app import create_app

    monkeypatch.setattr(settings, "viewer_token", _VIEWER_TOKEN)
    caplog.set_level(logging.INFO, logger="tasca.shell.api.app")
    create_app()

    assert "REST viewer authentication required=True" in caplog.text
    assert _VIEWER_TOKEN not in caplog.text


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

        # Missing both canonical title and legacy question returns standard envelope.
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "InvalidRequest"


# =============================================================================
# MCP Endpoint Availability Tests
# =============================================================================


def test_mcp_endpoint_mounted(mcp_test_client: Any) -> None:
    """Test that MCP endpoint is mounted at /mcp.

    Scenario: MCP Endpoint Mount
    Verifies that the MCP endpoint is accessible and responds to POST
    with a valid JSON-RPC response. Uses mcp_test_client (Starlette
    TestClient with lifespan) and Bearer auth for deterministic assertions.

    Note: Comprehensive MCP tests live in test_mcp.py. This test only
    verifies the endpoint is mounted and reachable.
    """
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {_ADMIN_TOKEN}",
    }
    response = mcp_test_client.post(
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
        headers=headers,
    )

    # MCP endpoint must respond 200 with SSE content when properly authenticated
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
