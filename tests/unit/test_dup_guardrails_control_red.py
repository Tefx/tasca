"""Expected-red guardrails for table control and sayings limit adoption.

These tests intentionally document pre-implementation drift for the
dup_guardrails.control-atomicity-red-tests step.  They are test-only and should
turn green when the downstream shared table-control operation and REST/MCP
adoptions land.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from returns.result import Failure

from tasca.core.domain.table import Table, TableId, TableStatus, Version
from tasca.shell.api.deps import get_db
from tasca.shell.api.routes.sayings import router as sayings_router
from tasca.shell.api.routes.tables import router as tables_router
from tasca.shell.storage.database import apply_schema
from tasca.shell.storage.table_repo import create_table


# Exact documented HTTP control payload shape from docs/tasca-http-api-v0.1.md
# section 3, Tables: body: {"action", "reason?", "dedup_id"}.  This fixture
# intentionally has no convenience-only speaker_name field.
DOC_HTTP_CONTROL_PAYLOAD = {
    "action": "close",
    "reason": "Completed",
    "dedup_id": "doc-http-control-dedup-1",
}

CANONICAL_CONTROL_CONTENT = "**CONTROL: CLOSE**\n\nReason: Completed"


@pytest.fixture
def conn() -> Generator[sqlite3.Connection, None, None]:
    """Create an in-memory database with the full schema."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    apply_schema(db)
    yield db
    db.close()


def _insert_table(db: sqlite3.Connection, table_id: str) -> None:
    now = datetime.now(UTC)
    table = Table(
        id=TableId(table_id),
        question=f"Question for {table_id}?",
        context=None,
        status=TableStatus.OPEN,
        version=Version(1),
        created_at=now,
        updated_at=now,
    )
    result = create_table(db, table)
    assert not isinstance(result, Failure)


def _tables_client(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from tasca.config import settings

    monkeypatch.setattr(settings, "admin_token", "test-admin-token")
    app = FastAPI()

    def get_test_db() -> Generator[sqlite3.Connection, None, None]:
        yield db

    app.dependency_overrides[get_db] = get_test_db
    app.include_router(tables_router, prefix="/tables")
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer test-admin-token"
    return client


def _sayings_client(db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from tasca.config import settings

    monkeypatch.setattr(settings, "admin_token", "test-admin-token")
    app = FastAPI()

    def get_test_db() -> Generator[sqlite3.Connection, None, None]:
        yield db

    app.dependency_overrides[get_db] = get_test_db
    app.include_router(sayings_router, prefix="/tables/{table_id}/sayings")
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer test-admin-token"
    return client


def test_mcp_control_rolls_back_control_saying_when_status_update_fails(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MCP table.control must be all-or-nothing when status update fails."""
    from tasca.shell.mcp import database, entrypoints

    _insert_table(conn, "mcp-atomicity-table")
    monkeypatch.setattr(database, "_mcp_db_connection", conn)

    conn.execute(
        """
        CREATE TRIGGER force_control_status_update_failure
        BEFORE UPDATE ON tables
        WHEN OLD.id = 'mcp-atomicity-table'
        BEGIN
            SELECT RAISE(ABORT, 'forced status update failure');
        END
        """
    )

    result = entrypoints.table_control(
        table_id="mcp-atomicity-table",
        action="pause",
        speaker_name="Admin",
        reason="forced failure after audit append",
        dedup_id="atomicity-red-1",
    )

    assert result["ok"] is False
    rows = conn.execute(
        "SELECT content FROM sayings WHERE table_id = ?",
        ("mcp-atomicity-table",),
    ).fetchall()
    assert rows == []


def test_rest_control_accepts_exact_documented_http_payload_shape(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REST control should accept the v0.1 documented body without extras."""
    _insert_table(conn, "rest-doc-shape-table")
    client = _tables_client(conn, monkeypatch)

    response = client.post(
        "/tables/rest-doc-shape-table/control",
        json=DOC_HTTP_CONTROL_PAYLOAD,
    )

    assert response.status_code == 200
    assert response.json() == {"table_status": "closed", "control_saying_sequence": 0}


def test_rest_and_mcp_control_reason_format_converges_on_canonical_rule(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REST and MCP control audit content should use one canonical format."""
    from tasca.shell.mcp import database, entrypoints

    _insert_table(conn, "rest-format-table")
    _insert_table(conn, "mcp-format-table")
    client = _tables_client(conn, monkeypatch)
    monkeypatch.setattr(database, "_mcp_db_connection", conn)

    rest_response = client.post(
        "/tables/rest-format-table/control",
        json={"action": "close", "speaker_name": "Admin", "reason": "Completed"},
    )
    assert rest_response.status_code == 200

    mcp_response = entrypoints.table_control(
        table_id="mcp-format-table",
        action="close",
        speaker_name="Admin",
        reason="Completed",
        dedup_id="format-red-1",
    )
    assert mcp_response["ok"] is True

    rows = conn.execute(
        "SELECT table_id, content FROM sayings WHERE table_id IN (?, ?) ORDER BY table_id",
        ("mcp-format-table", "rest-format-table"),
    ).fetchall()
    contents = {table_id: content for table_id, content in rows}

    assert contents["rest-format-table"] == CANONICAL_CONTROL_CONTENT
    assert contents["mcp-format-table"] == CANONICAL_CONTROL_CONTENT


def test_rest_sayings_limit_failure_preserves_status_error_and_body_semantics(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REST limit failures remain a 400 with the standard error envelope."""
    from tasca.config import settings

    _insert_table(conn, "limits-table")
    monkeypatch.setattr(settings, "max_content_length", 5)
    client = _sayings_client(conn, monkeypatch)

    response = client.post(
        "/tables/limits-table/sayings",
        json={"speaker_name": "Admin", "content": "123456"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "error": {
            "code": "LimitExceeded",
            "message": "content exceeds limit: 6 > 5",
            "details": {
                "error": "limit_exceeded",
                "limit_kind": "content",
                "limit": 5,
                "actual": 6,
                "message": "content exceeds limit: 6 > 5",
            },
        },
    }
