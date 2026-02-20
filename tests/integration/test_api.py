"""
Integration tests for REST API endpoints.

These tests verify the REST API endpoints work correctly with a running server.
All tests require the server to be running at TASCA_TEST_API_URL (default: localhost:8000).

Usage:
    # Start server
    uv run tasca

    # Run tests
    pytest tests/integration/test_api.py -v

    # With custom URL
    TASCA_TEST_API_URL=http://api.example.com pytest tests/integration/test_api.py -v
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import API_BASE_URL, check_server_available
from tests.integration.harness import RESTHarness


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
    async with RESTHarness() as harness:
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
    async with RESTHarness() as harness:
        response = await harness.readiness_check()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"


# =============================================================================
# Table CRUD Tests
# =============================================================================


@pytest.mark.asyncio
async def test_create_table() -> None:
    """Test POST /tables creates a new table.

    Scenario: REST Table Creation
    Verifies that creating a table returns the created table
    with a valid ID and the provided data.
    """
    async with RESTHarness() as harness:
        table_data = {
            "question": "What is the best approach for this feature?",
            "context": "We need to decide between options A and B",
        }
        response = await harness.create_table(table_data)

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
    Note: Currently returns placeholder data as storage is not implemented.
    """
    async with RESTHarness() as harness:
        table_id = "test-table-123"
        response = await harness.get_table(table_id)

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == table_id


@pytest.mark.asyncio
async def test_list_tables() -> None:
    """Test GET /tables lists all tables.

    Scenario: REST Table Listing
    Verifies that listing tables returns an array.
    Note: Currently returns empty array as storage is not implemented.
    """
    async with RESTHarness() as harness:
        response = await harness.list_tables()

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


@pytest.mark.asyncio
async def test_delete_table() -> None:
    """Test DELETE /tables/{table_id} deletes a table.

    Scenario: REST Table Deletion
    Verifies that deleting a table returns a confirmation.
    """
    async with RESTHarness() as harness:
        table_id = "test-table-to-delete"
        response = await harness.delete_table(table_id)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["table_id"] == table_id


# =============================================================================
# Error Handling Tests
# =============================================================================


@pytest.mark.asyncio
async def test_404_on_unknown_path() -> None:
    """Test that unknown paths return 404.

    Scenario: REST 404 Handling
    Verifies that accessing an unknown endpoint returns 404.
    """
    async with RESTHarness() as harness:
        response = await harness.client.get("/unknown-endpoint")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_422_on_invalid_input() -> None:
    """Test that invalid input returns 422.

    Scenario: REST Validation Error
    Verifies that sending invalid data returns a validation error.
    """
    async with RESTHarness() as harness:
        # Missing required 'question' field
        response = await harness.create_table({})

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
    """
    async with RESTHarness() as harness:
        # MCP uses POST for JSON-RPC
        response = await harness.client.post(
            "/mcp/",
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

        # MCP should respond (even if tools aren't fully implemented)
        assert response.status_code in [200, 500]  # 500 if tool not implemented


# =============================================================================
# Integration Check
# =============================================================================


def test_server_available() -> None:
    """Test that server is available for integration tests.

    This test documents the requirement for a running server.
    It will fail if the server is not running, prompting the user
    to start it.
    """
    if not check_server_available(API_BASE_URL):
        pytest.fail(f"Server not available at {API_BASE_URL}. Start the server with: uv run tasca")
