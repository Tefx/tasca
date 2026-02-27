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
        # Authentication is always required now (admin_token auto-generated)
        assert response.status_code == 401

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
        assert response.status_code == 401

    def test_update_table_not_found(self, admin_client: TestClient) -> None:
        """Update non-existent table returns 404."""
        response = admin_client.put(
            "/tables/nonexistent-id?expected_version=1",
            json={"question": "Updated?", "context": None, "status": "open"},
        )
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_put_without_status_change_updates_metadata(self, admin_client: TestClient) -> None:
        """Update table with correct version succeeds (same status)."""
        # Create a table
        create_response = admin_client.post(
            "/tables",
            json={"question": "Original question?", "context": "Original context"},
        )
        table_id = create_response.json()["id"]

        # Update the table (keeping same status "open")
        response = admin_client.put(
            f"/tables/{table_id}?expected_version=1",
            json={
                "question": "Updated question?",
                "context": "Updated context",
                "status": "open",  # Same status as created
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["question"] == "Updated question?"
        assert data["context"] == "Updated context"
        assert data["status"] == "open"
        assert data["version"] == 2

    def test_put_with_status_change_returns_400(self, admin_client: TestClient) -> None:
        """Update with different status returns 400 Bad Request."""
        # Create a table (status is "open")
        create_response = admin_client.post(
            "/tables",
            json={"question": "Original question?", "context": "Original context"},
        )
        table_id = create_response.json()["id"]

        # Try to change status to "paused"
        response = admin_client.put(
            f"/tables/{table_id}?expected_version=1",
            json={
                "question": "Updated question?",
                "context": "Updated context",
                "status": "paused",  # Different status
            },
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "status changes are not allowed" in detail.lower()
        assert "POST /tables/{table_id}/control" in detail

    def test_put_status_change_closed_to_open_returns_400(self, admin_client: TestClient) -> None:
        """PUT cannot change status from closed to open - must use control endpoint."""
        # Create a table and close it
        create_response = admin_client.post(
            "/tables",
            json={"question": "Test question?"},
        )
        table_id = create_response.json()["id"]

        # Close the table via control endpoint
        admin_client.post(
            f"/tables/{table_id}/control",
            json={"action": "close", "speaker_name": "Admin"},
        )

        # Get current version
        get_response = admin_client.get(f"/tables/{table_id}")
        current_version = get_response.json()["version"]

        # Try to change status back to "open" via PUT
        response = admin_client.put(
            f"/tables/{table_id}?expected_version={current_version}",
            json={
                "question": "Updated question?",
                "context": None,
                "status": "open",  # Trying to change status
            },
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert "status changes are not allowed" in detail.lower()

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

    def test_update_table_context_required(self, admin_client: TestClient) -> None:
        """Update requires context field - prevents accidental clearing.

        This test verifies the fix for PUT context semantics:
        - Omitting context should return 422 (validation error)
        - This prevents accidental context clearing when caller forgets the field
        """
        # Create a table with context
        create_response = admin_client.post(
            "/tables",
            json={"question": "Original question?", "context": "Original context"},
        )
        table_id = create_response.json()["id"]

        # Try to update without context field (should fail)
        response = admin_client.put(
            f"/tables/{table_id}?expected_version=1",
            json={
                "question": "Updated question?",
                # context omitted - should be rejected
                "status": "open",
            },
        )
        assert response.status_code == 422  # Validation error
        detail = response.json()["detail"]
        # Pydantic v2 validation error format
        assert any("context" in str(err).lower() for err in detail)

    def test_update_table_explicit_null_clears_context(self, admin_client: TestClient) -> None:
        """Explicit null for context clears the context (full replace semantics)."""
        # Create a table with context
        create_response = admin_client.post(
            "/tables",
            json={"question": "Original question?", "context": "Original context"},
        )
        table_id = create_response.json()["id"]

        # Update with explicit null to clear context
        response = admin_client.put(
            f"/tables/{table_id}?expected_version=1",
            json={
                "question": "Updated question?",
                "context": None,  # Explicit null - should clear
                "status": "open",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["question"] == "Updated question?"
        assert data["context"] is None  # Context cleared
        assert data["version"] == 2

    def test_update_table_keeps_context_with_string(self, admin_client: TestClient) -> None:
        """Explicit string value keeps/updates context (full replace semantics)."""
        # Create a table with context
        create_response = admin_client.post(
            "/tables",
            json={"question": "Original question?", "context": "Original context"},
        )
        table_id = create_response.json()["id"]

        # Update with new context string
        response = admin_client.put(
            f"/tables/{table_id}?expected_version=1",
            json={
                "question": "Updated question?",
                "context": "Updated context",  # Explicit string - updates
                "status": "open",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["question"] == "Updated question?"
        assert data["context"] == "Updated context"
        assert data["version"] == 2


# =============================================================================
# DELETE /tables/{table_id} - Delete Tests
# =============================================================================


class TestDeleteTable:
    """Tests for DELETE /tables/{table_id} endpoint."""

    def test_delete_table_requires_auth(self, client: TestClient) -> None:
        """Delete table requires admin authentication."""
        response = client.delete("/tables/some-id")
        # Authentication is always required now (admin_token auto-generated)
        assert response.status_code == 401

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
# POST /tables/actions/batch-delete - Batch Delete Tests
# =============================================================================


def _close_table(admin_client: TestClient, table_id: str) -> None:
    """Helper to close a table via the control endpoint."""
    admin_client.post(
        f"/tables/{table_id}/control",
        json={"action": "close", "speaker_name": "test"},
    )


class TestBatchDeleteTables:
    """Tests for POST /tables/actions/batch-delete endpoint."""

    def test_batch_delete_requires_auth(self, client: TestClient) -> None:
        response = client.post(
            "/tables/actions/batch-delete",
            json={"ids": ["some-id"]},
        )
        assert response.status_code == 401

    def test_batch_delete_closed_tables(self, admin_client: TestClient) -> None:
        """Batch delete succeeds for closed tables."""
        # Create and close 2 tables
        ids = []
        for _ in range(2):
            r = admin_client.post("/tables", json={"question": "To delete?"})
            tid = r.json()["id"]
            _close_table(admin_client, tid)
            ids.append(tid)

        # Batch delete
        response = admin_client.post(
            "/tables/actions/batch-delete",
            json={"ids": ids},
        )
        assert response.status_code == 200
        data = response.json()
        assert set(data["deleted_ids"]) == set(ids)

        # Verify tables are gone
        for tid in ids:
            assert admin_client.get(f"/tables/{tid}").status_code == 404

    def test_batch_delete_rejects_open_table(self, admin_client: TestClient) -> None:
        """Batch delete fails with 409 if any table is not closed."""
        r = admin_client.post("/tables", json={"question": "Still open"})
        tid = r.json()["id"]

        response = admin_client.post(
            "/tables/actions/batch-delete",
            json={"ids": [tid]},
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["error"] == "BATCH_PRECONDITION_FAILED"
        assert any(d["id"] == tid and d["reason"] == "TABLE_NOT_CLOSED" for d in detail["details"])

    def test_batch_delete_rejects_not_found(self, admin_client: TestClient) -> None:
        """Batch delete fails with 409 if any table ID doesn't exist."""
        response = admin_client.post(
            "/tables/actions/batch-delete",
            json={"ids": ["nonexistent-id"]},
        )
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["error"] == "BATCH_PRECONDITION_FAILED"
        assert detail["details"][0]["reason"] == "NOT_FOUND"

    def test_batch_delete_mixed_rejection(self, admin_client: TestClient) -> None:
        """All-or-nothing: closed + open in same batch → entire batch rejected."""
        r_closed = admin_client.post("/tables", json={"question": "Closed"})
        closed_id = r_closed.json()["id"]
        _close_table(admin_client, closed_id)

        r_open = admin_client.post("/tables", json={"question": "Open"})
        open_id = r_open.json()["id"]

        response = admin_client.post(
            "/tables/actions/batch-delete",
            json={"ids": [closed_id, open_id]},
        )
        assert response.status_code == 409

        # Closed table should still exist (nothing deleted)
        assert admin_client.get(f"/tables/{closed_id}").status_code == 200

    def test_batch_delete_empty_ids(self, admin_client: TestClient) -> None:
        """Empty ids list returns 422 validation error."""
        response = admin_client.post(
            "/tables/actions/batch-delete",
            json={"ids": []},
        )
        assert response.status_code == 422


# =============================================================================
# Integration Flow Tests
# =============================================================================


class TestTableFlow:
    """End-to-end flow tests for table operations."""

    def test_create_get_update_delete_flow(self, admin_client: TestClient) -> None:
        """Full CRUD flow for a table (status changes via control endpoint only)."""
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

        # Update (keeping same status "open")
        update_response = admin_client.put(
            f"/tables/{table_id}?expected_version=1",
            json={
                "question": "Updated question?",
                "context": "Updated context",
                "status": "open",  # Same status
            },
        )
        assert update_response.status_code == 200
        assert update_response.json()["version"] == 2

        # Update again with new version (still same status)
        update_response2 = admin_client.put(
            f"/tables/{table_id}?expected_version=2",
            json={
                "question": "Final question?",
                "context": None,
                "status": "open",  # Same status
            },
        )
        assert update_response2.status_code == 200
        assert update_response2.json()["version"] == 3
        assert update_response2.json()["status"] == "open"

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


# =============================================================================
# POST /tables/{table_id}/control - Control Tests
# =============================================================================


class TestControlTable:
    """Tests for POST /tables/{table_id}/control endpoint."""

    def test_control_requires_admin_auth(self, client: TestClient) -> None:
        """Control endpoint requires admin authentication."""
        response = client.post(
            "/tables/some-id/control",
            json={"action": "close", "speaker_name": "Admin"},
        )
        assert response.status_code == 401

    def test_control_invalid_action_returns_400(self, admin_client: TestClient) -> None:
        """Invalid action returns 400."""
        # Create a table first
        create_response = admin_client.post(
            "/tables",
            json={"question": "Test question?"},
        )
        table_id = create_response.json()["id"]

        # Try invalid action
        response = admin_client.post(
            f"/tables/{table_id}/control",
            json={"action": "invalid_action", "speaker_name": "Admin"},
        )
        assert response.status_code == 400
        assert "invalid action" in response.json()["detail"].lower()

    def test_control_close_creates_control_saying_and_updates_status(
        self, admin_client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Close action updates table status and creates CONTROL saying."""
        # Create a table
        create_response = admin_client.post(
            "/tables",
            json={"question": "Test question?"},
        )
        table_id = create_response.json()["id"]

        # Close the table
        response = admin_client.post(
            f"/tables/{table_id}/control",
            json={"action": "close", "speaker_name": "Admin", "reason": "Completed"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["table_status"] == "closed"
        assert isinstance(data["control_saying_sequence"], int)

        # Verify table status is closed
        get_response = admin_client.get(f"/tables/{table_id}")
        assert get_response.json()["status"] == "closed"

        # Verify CONTROL saying exists via direct DB query
        cursor = test_db.execute(
            "SELECT content, speaker_kind, speaker_name FROM sayings WHERE table_id = ?",
            (table_id,),
        )
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert "**CONTROL: CLOSE**" in rows[0][0]
        assert "Completed" in rows[0][0]
        assert rows[0][1] == "human"  # speaker_kind
        assert rows[0][2] == "Admin"  # speaker_name

    def test_control_close_on_closed_table_returns_409(self, admin_client: TestClient) -> None:
        """Closing an already closed table returns 409."""
        # Create a table and close it
        create_response = admin_client.post(
            "/tables",
            json={"question": "Test question?"},
        )
        table_id = create_response.json()["id"]

        admin_client.post(
            f"/tables/{table_id}/control",
            json={"action": "close", "speaker_name": "Admin"},
        )

        # Try to close again
        response = admin_client.post(
            f"/tables/{table_id}/control",
            json={"action": "close", "speaker_name": "Admin"},
        )
        assert response.status_code == 409
        assert "cannot close" in response.json()["detail"].lower()

    def test_control_pause_from_open(self, admin_client: TestClient) -> None:
        """Pause action from open state succeeds."""
        # Create a table
        create_response = admin_client.post(
            "/tables",
            json={"question": "Test question?"},
        )
        table_id = create_response.json()["id"]

        # Pause the table
        response = admin_client.post(
            f"/tables/{table_id}/control",
            json={"action": "pause", "speaker_name": "Admin"},
        )
        assert response.status_code == 200
        assert response.json()["table_status"] == "paused"

    def test_control_resume_from_paused(self, admin_client: TestClient) -> None:
        """Resume action from paused state succeeds."""
        # Create a table and pause it
        create_response = admin_client.post(
            "/tables",
            json={"question": "Test question?"},
        )
        table_id = create_response.json()["id"]

        admin_client.post(
            f"/tables/{table_id}/control",
            json={"action": "pause", "speaker_name": "Admin"},
        )

        # Resume the table
        response = admin_client.post(
            f"/tables/{table_id}/control",
            json={"action": "resume", "speaker_name": "Admin"},
        )
        assert response.status_code == 200
        assert response.json()["table_status"] == "open"

    def test_control_pause_then_resume(self, admin_client: TestClient) -> None:
        """Combined pause->resume flow test verifying full state transitions."""
        # Create a table (starts as open)
        create_response = admin_client.post(
            "/tables",
            json={"question": "Test question?"},
        )
        table_id = create_response.json()["id"]
        assert create_response.json()["status"] == "open"

        # Pause the table
        pause_response = admin_client.post(
            f"/tables/{table_id}/control",
            json={"action": "pause", "speaker_name": "Admin"},
        )
        assert pause_response.status_code == 200
        assert pause_response.json()["table_status"] == "paused"

        # Verify table is paused
        get_response = admin_client.get(f"/tables/{table_id}")
        assert get_response.json()["status"] == "paused"

        # Resume the table
        resume_response = admin_client.post(
            f"/tables/{table_id}/control",
            json={"action": "resume", "speaker_name": "Admin"},
        )
        assert resume_response.status_code == 200
        assert resume_response.json()["table_status"] == "open"

        # Verify table is back to open
        get_response2 = admin_client.get(f"/tables/{table_id}")
        assert get_response2.json()["status"] == "open"

    def test_control_resume_from_open_returns_409(self, admin_client: TestClient) -> None:
        """Resume action from open state returns 409."""
        # Create a table (starts as open)
        create_response = admin_client.post(
            "/tables",
            json={"question": "Test question?"},
        )
        table_id = create_response.json()["id"]

        # Try to resume (table is already open)
        response = admin_client.post(
            f"/tables/{table_id}/control",
            json={"action": "resume", "speaker_name": "Admin"},
        )
        assert response.status_code == 409
        assert "cannot resume" in response.json()["detail"].lower()

    def test_control_pause_from_closed_returns_409(self, admin_client: TestClient) -> None:
        """Pause action from closed state returns 409."""
        # Create a table and close it
        create_response = admin_client.post(
            "/tables",
            json={"question": "Test question?"},
        )
        table_id = create_response.json()["id"]

        admin_client.post(
            f"/tables/{table_id}/control",
            json={"action": "close", "speaker_name": "Admin"},
        )

        # Try to pause
        response = admin_client.post(
            f"/tables/{table_id}/control",
            json={"action": "pause", "speaker_name": "Admin"},
        )
        assert response.status_code == 409

    def test_control_table_not_found(self, admin_client: TestClient) -> None:
        """Control on non-existent table returns 404."""
        response = admin_client.post(
            "/tables/nonexistent-id/control",
            json={"action": "close", "speaker_name": "Admin"},
        )
        assert response.status_code == 404

    def test_control_version_conflict_returns_structured_error(
        self, test_db: sqlite3.Connection
    ) -> None:
        """Version conflict at storage layer returns structured error.

        Tests the storage layer directly since the API refetches tables.
        The typed error (ControlVersionConflictError) ensures the route layer
        can detect conflicts via isinstance() instead of string matching.
        """
        from datetime import datetime

        from returns.result import Failure

        from tasca.core.domain.saying import Speaker, SpeakerKind
        from tasca.core.domain.table import Table, TableId, TableStatus, Version
        from tasca.shell.storage.control_repo import (
            ControlVersionConflictError,
            atomic_control_table,
        )

        # Create a table directly in DB
        table_id = "test-table-id"
        test_db.execute(
            """
            INSERT INTO tables (id, question, context, status, version, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (table_id, "Test?", None, "open", 1, "2024-01-01T12:00:00", "2024-01-01T12:00:00"),
        )
        test_db.commit()

        # Simulate concurrent modification: update version in DB
        test_db.execute("UPDATE tables SET version = 5 WHERE id = ?", (table_id,))
        test_db.commit()

        # Create a stale Table object (version=1, but DB has version=5)
        stale_table = Table(
            id=TableId(table_id),
            question="Test?",
            context=None,
            status=TableStatus.OPEN,
            version=Version(1),  # Stale - DB has version 5
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            updated_at=datetime(2024, 1, 1, 12, 0, 0),
        )

        speaker = Speaker(kind=SpeakerKind.HUMAN, name="Admin")
        result = atomic_control_table(
            conn=test_db,
            table_id=table_id,
            speaker=speaker,
            control_content="**CONTROL: CLOSE**",
            new_status=TableStatus.CLOSED,
            current_table=stale_table,
            now=datetime(2024, 1, 1, 13, 0, 0),
        )

        # Verify we get the typed error (not a string)
        assert isinstance(result, Failure)
        error = result.failure()
        assert isinstance(error, ControlVersionConflictError)
        assert error.table_id == table_id
        assert error.expected_version == 1

        # Verify to_json returns proper structure for API response
        json_data = error.to_json()
        assert json_data["error"] == "version_conflict"
        assert json_data["table_id"] == table_id
        assert json_data["expected_version"] == 1
        assert "message" in json_data
