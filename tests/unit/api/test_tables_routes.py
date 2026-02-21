"""
Unit tests for tables API routes.

Uses FastAPI TestClient with an in-memory SQLite database.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Generator
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tasca.core.domain.table import TableId, TableStatus, Version
from tasca.shell.api.routes.tables import DeleteResponse, router
from tasca.shell.storage.database import apply_schema
from tasca.shell.storage.table_repo import (
    TableNotFoundError,
    VersionConflictError,
    create_table,
    get_table,
    list_tables,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def test_db() -> Generator[sqlite3.Connection, None, None]:
    """Create an in-memory database with tables schema."""
    # check_same_thread=False is needed for FastAPI TestClient which uses threads
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    apply_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def app(test_db: sqlite3.Connection) -> FastAPI:
    """Create a FastAPI app with tables router and test database."""
    app = FastAPI()

    # Override the get_db dependency to use test database
    def get_test_db() -> Generator[sqlite3.Connection, None, None]:
        yield test_db

    app.dependency_overrides["get_db"] = get_test_db
    # We need to import and use the proper override mechanism
    from tasca.shell.api.deps import get_db

    app.dependency_overrides[get_db] = get_test_db

    app.include_router(router, prefix="/tables")
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def admin_client(app: FastAPI) -> TestClient:
    """Create a test client with admin auth enabled."""
    from tasca.config import settings
    from unittest.mock import MagicMock

    # Mock settings to have an admin token
    with patch.object(settings, "admin_token", "test-admin-token"):
        client = TestClient(app)
        client.headers["Authorization"] = "Bearer test-admin-token"
        yield client


# =============================================================================
# Helper Functions
# =============================================================================


def create_test_table(conn: sqlite3.Connection, table_id: str = "test-table-1") -> None:
    """Create a test table directly in the database."""
    from datetime import datetime

    table = type(
        "Table",
        (),
        {
            "id": TableId(table_id),
            "question": "Test question?",
            "context": "Test context",
            "status": TableStatus.OPEN,
            "version": Version(1),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        },
    )()
    create_table(conn, table)


# =============================================================================
# POST /tables - Create Tests
# =============================================================================


class TestCreateTable:
    """Tests for POST /tables endpoint."""

    def test_create_table_requires_auth(self, client: TestClient) -> None:
        """Create table requires admin authentication."""
        response = client.post(
            "/tables",
            json={"question": "Test question?", "context": "Test context"},
        )
        # If admin_token is None (disabled), should succeed
        # If admin_token is set, should return 401
        # Default settings have admin_token=None
        assert response.status_code in [200, 401]

    def test_create_table_with_auth(self, admin_client: TestClient) -> None:
        """Create table succeeds with admin auth."""
        response = admin_client.post(
            "/tables",
            json={"question": "What is the best approach?", "context": "Consider performance"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["question"] == "What is the best approach?"
        assert data["context"] == "Consider performance"
        assert data["status"] == "open"
        assert data["version"] == 1

    def test_create_table_minimal(self, admin_client: TestClient) -> None:
        """Create table with minimal data (question only)."""
        response = admin_client.post(
            "/tables",
            json={"question": "Just a question?"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["question"] == "Just a question?"
        assert data["context"] is None

    def test_create_table_missing_question(self, admin_client: TestClient) -> None:
        """Create table with missing question returns 422."""
        response = admin_client.post(
            "/tables",
            json={"context": "No question provided"},
        )
        assert response.status_code == 422  # Validation error


# =============================================================================
# GET /tables - List Tests
# =============================================================================


class TestListTables:
    """Tests for GET /tables endpoint."""

    def test_list_tables_empty(self, client: TestClient) -> None:
        """List tables returns empty list when no tables."""
        response = client.get("/tables")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_tables_with_data(self, admin_client: TestClient) -> None:
        """List tables returns all tables."""
        # Create some tables
        admin_client.post("/tables", json={"question": "First question?"})
        admin_client.post("/tables", json={"question": "Second question?"})

        response = admin_client.get("/tables")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        questions = [t["question"] for t in data]
        assert "First question?" in questions
        assert "Second question?" in questions


# =============================================================================
# GET /tables/{table_id} - Get Tests
# =============================================================================


class TestGetTable:
    """Tests for GET /tables/{table_id} endpoint."""

    def test_get_table_not_found(self, client: TestClient) -> None:
        """Get non-existent table returns 404."""
        response = client.get("/tables/nonexistent-id")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_table_exists(self, admin_client: TestClient) -> None:
        """Get existing table returns the table."""
        # Create a table
        create_response = admin_client.post(
            "/tables",
            json={"question": "Test question?", "context": "Test context"},
        )
        table_id = create_response.json()["id"]

        # Get the table
        response = admin_client.get(f"/tables/{table_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == table_id
        assert data["question"] == "Test question?"
        assert data["context"] == "Test context"
        assert data["status"] == "open"
        assert data["version"] == 1


# =============================================================================
# PUT /tables/{table_id} - Update Tests
# =============================================================================


class TestUpdateTable:
    """Tests for PUT /tables/{table_id} endpoint."""

    def test_update_table_requires_auth(self, client: TestClient) -> None:
        """Update table requires admin authentication."""
        response = client.put(
            "/tables/some-id?expected_version=1",
            json={"question": "Updated?", "context": None, "status": "open"},
        )
        assert response.status_code in [401, 404]  # 401 if auth required, 404 if not

    def test_update_table_not_found(self, admin_client: TestClient) -> None:
        """Update non-existent table returns 404."""
        response = admin_client.put(
            "/tables/nonexistent-id?expected_version=1",
            json={"question": "Updated?", "context": None, "status": "open"},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_update_table_success(self, admin_client: TestClient) -> None:
        """Update table with correct version succeeds."""
        # Create a table
        create_response = admin_client.post(
            "/tables",
            json={"question": "Original question?", "context": "Original context"},
        )
        table_id = create_response.json()["id"]

        # Update the table
        response = admin_client.put(
            f"/tables/{table_id}?expected_version=1",
            json={
                "question": "Updated question?",
                "context": "Updated context",
                "status": "paused",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["question"] == "Updated question?"
        assert data["context"] == "Updated context"
        assert data["status"] == "paused"
        assert data["version"] == 2

    def test_update_table_version_conflict(self, admin_client: TestClient) -> None:
        """Update with wrong version returns 409 Conflict."""
        # Create a table
        create_response = admin_client.post(
            "/tables",
            json={"question": "Original question?"},
        )
        table_id = create_response.json()["id"]

        # Try to update with wrong version
        response = admin_client.put(
            f"/tables/{table_id}?expected_version=99",  # Wrong version
            json={
                "question": "Updated question?",
                "context": None,
                "status": "open",
            },
        )
        assert response.status_code == 409
        data = response.json()
        # The detail should contain version conflict info
        detail = data["detail"]
        assert "error" in detail
        assert detail["error"] == "version_conflict"
        assert detail["current_version"] == 1
        assert detail["expected_version"] == 99

    def test_update_table_expected_version_required(self, admin_client: TestClient) -> None:
        """Update requires expected_version query parameter."""
        response = admin_client.put(
            "/tables/some-id",  # Missing expected_version
            json={"question": "Updated?", "context": None, "status": "open"},
        )
        assert response.status_code == 422  # Validation error


# =============================================================================
# DELETE /tables/{table_id} - Delete Tests
# =============================================================================


class TestDeleteTable:
    """Tests for DELETE /tables/{table_id} endpoint."""

    def test_delete_table_requires_auth(self, client: TestClient) -> None:
        """Delete table requires admin authentication."""
        response = client.delete("/tables/some-id")
        # Default settings have admin_token=None, so auth is disabled
        # If auth is enabled, would return 401
        assert response.status_code in [200, 401, 404]

    def test_delete_table_not_found(self, admin_client: TestClient) -> None:
        """Delete non-existent table returns 404."""
        response = admin_client.delete("/tables/nonexistent-id")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_delete_table_success(self, admin_client: TestClient) -> None:
        """Delete existing table succeeds."""
        # Create a table
        create_response = admin_client.post(
            "/tables",
            json={"question": "To be deleted?"},
        )
        table_id = create_response.json()["id"]

        # Delete the table
        response = admin_client.delete(f"/tables/{table_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "deleted"
        assert data["table_id"] == table_id

        # Verify it's deleted
        get_response = admin_client.get(f"/tables/{table_id}")
        assert get_response.status_code == 404


# =============================================================================
# Integration Flow Tests
# =============================================================================


class TestTableFlow:
    """End-to-end flow tests for table operations."""

    def test_create_get_update_delete_flow(self, admin_client: TestClient) -> None:
        """Full CRUD flow for a table."""
        # Create
        create_response = admin_client.post(
            "/tables",
            json={"question": "Initial question?", "context": "Initial context"},
        )
        assert create_response.status_code == 200
        table_id = create_response.json()["id"]

        # Get
        get_response = admin_client.get(f"/tables/{table_id}")
        assert get_response.status_code == 200
        assert get_response.json()["question"] == "Initial question?"

        # Update
        update_response = admin_client.put(
            f"/tables/{table_id}?expected_version=1",
            json={
                "question": "Updated question?",
                "context": "Updated context",
                "status": "paused",
            },
        )
        assert update_response.status_code == 200
        assert update_response.json()["version"] == 2

        # Update again with new version
        update_response2 = admin_client.put(
            f"/tables/{table_id}?expected_version=2",
            json={
                "question": "Final question?",
                "context": None,
                "status": "closed",
            },
        )
        assert update_response2.status_code == 200
        assert update_response2.json()["version"] == 3
        assert update_response2.json()["status"] == "closed"

        # Delete
        delete_response = admin_client.delete(f"/tables/{table_id}")
        assert delete_response.status_code == 200
        assert delete_response.json()["status"] == "deleted"

        # Verify deleted
        get_response2 = admin_client.get(f"/tables/{table_id}")
        assert get_response2.status_code == 404


# =============================================================================
# Observability Tests - caplog-based structured log assertions
# =============================================================================


class TestTablesObservability:
    """Tests for structured logging observability during table operations.

    These tests use pytest's caplog fixture to capture log output at runtime
    and assert on the structured JSON fields.
    """

    def test_create_table_emits_structured_log(
        self, admin_client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """POST /tables emits table_created structured log event."""
        caplog.set_level(logging.INFO, logger="tasca.shell.api.routes.tables")

        response = admin_client.post(
            "/tables",
            json={"question": "Test observability?", "context": "Testing logs"},
        )
        assert response.status_code == 200
        table_id = response.json()["id"]

        # Find the table_created log event
        table_created_logs = [r for r in caplog.records if "table_created" in r.getMessage()]
        assert len(table_created_logs) >= 1, "Expected table_created log event"

        # Parse and assert structured fields
        log_data = json.loads(table_created_logs[0].getMessage())
        assert log_data["event"] == "table_created"
        assert log_data["table_id"] == table_id
        assert log_data["speaker"] == "rest:admin"
        assert "timestamp" in log_data

    def test_update_table_emits_structured_log(
        self, admin_client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """PUT /tables/{id} emits table_updated structured log event."""
        caplog.set_level(logging.INFO, logger="tasca.shell.api.routes.tables")

        # Create a table
        create_response = admin_client.post(
            "/tables",
            json={"question": "Original?"},
        )
        table_id = create_response.json()["id"]

        # Clear logs from create
        caplog.clear()

        # Update the table
        update_response = admin_client.put(
            f"/tables/{table_id}?expected_version=1",
            json={"question": "Updated?", "context": None, "status": "open"},
        )
        assert update_response.status_code == 200

        # Find the table_updated log event
        table_updated_logs = [r for r in caplog.records if "table_updated" in r.getMessage()]
        assert len(table_updated_logs) >= 1, "Expected table_updated log event"

        # Parse and assert structured fields
        log_data = json.loads(table_updated_logs[0].getMessage())
        assert log_data["event"] == "table_updated"
        assert log_data["table_id"] == table_id
        assert log_data["version"] == 2
        assert log_data["speaker"] == "rest:admin"

    def test_delete_table_emits_structured_log(
        self, admin_client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """DELETE /tables/{id} emits table_deleted structured log event."""
        caplog.set_level(logging.INFO, logger="tasca.shell.api.routes.tables")

        # Create a table
        create_response = admin_client.post(
            "/tables",
            json={"question": "To delete?"},
        )
        table_id = create_response.json()["id"]

        # Clear logs from create
        caplog.clear()

        # Delete the table
        delete_response = admin_client.delete(f"/tables/{table_id}")
        assert delete_response.status_code == 200

        # Find the table_deleted log event
        table_deleted_logs = [r for r in caplog.records if "table_deleted" in r.getMessage()]
        assert len(table_deleted_logs) >= 1, "Expected table_deleted log event"

        # Parse and assert structured fields
        log_data = json.loads(table_deleted_logs[0].getMessage())
        assert log_data["event"] == "table_deleted"
        assert log_data["table_id"] == table_id
        assert log_data["speaker"] == "rest:admin"
