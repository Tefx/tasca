"""Atomic idempotency regressions for MCP table_say."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from returns.result import Failure, Success

from tasca.core.domain.table import Table, TableId, TableStatus
from tasca.shell.mcp import entrypoints
from tasca.shell.storage.database import apply_schema
from tasca.shell.storage.table_repo import create_table


def _create_database(path: str = ":memory:") -> tuple[sqlite3.Connection, str]:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    assert isinstance(apply_schema(conn), Success)
    table_id = f"idempotency-{uuid.uuid4()}"
    table = Table(
        id=TableId(table_id),
        question="Is table_say idempotent?",
        status=TableStatus.OPEN,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert isinstance(create_table(conn, table), Success)
    return conn, table_id


def test_idempotency_storage_failure_rolls_back_saying_attachments_and_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn, table_id = _create_database()
    monkeypatch.setattr(entrypoints, "get_mcp_db", lambda: iter((conn,)))
    conn.execute(
        """
        CREATE TRIGGER fail_table_say_idempotency
        BEFORE INSERT ON idempotency_keys
        WHEN NEW.tool_name = 'table_say'
        BEGIN
            SELECT RAISE(ABORT, 'injected idempotency storage failure');
        END
        """
    )
    conn.commit()
    request = {
        "table_id": table_id,
        "content": "Atomic body",
        "speaker_name": "Alice",
        "speaker_kind": "human",
        "dedup_id": "forced-storage-failure",
        "attachments": [{"name": "empty.md", "content": ""}],
    }

    failed = entrypoints.table_say(**request)

    assert isinstance(failed, Failure)
    assert failed.failure()["error"]["code"] == "DATABASE_ERROR"
    assert "store idempotency key" in failed.failure()["error"]["message"]
    assert conn.execute("SELECT COUNT(*) FROM sayings").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM saying_attachments").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 0

    conn.execute("DROP TRIGGER fail_table_say_idempotency")
    conn.commit()
    retried = entrypoints.table_say(**request)

    assert isinstance(retried, Success)
    assert retried.unwrap()["data"]["sequence"] == 0
    assert conn.execute("SELECT COUNT(*) FROM sayings").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM saying_attachments").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 1
    conn.close()


def test_concurrent_same_dedup_id_commits_one_attachment_bearing_saying(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "concurrent-idempotency.db"
    setup_conn, table_id = _create_database(str(db_path))
    setup_conn.execute("PRAGMA journal_mode=WAL")
    setup_conn.close()

    opened_connections: list[sqlite3.Connection] = []
    connections_lock = threading.Lock()

    def get_thread_connection():
        conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        with connections_lock:
            opened_connections.append(conn)
        return iter((conn,))

    monkeypatch.setattr(entrypoints, "get_mcp_db", get_thread_connection)
    request = {
        "table_id": table_id,
        "content": "Concurrent body",
        "speaker_name": "Alice",
        "speaker_kind": "human",
        "dedup_id": "same-concurrent-key",
        "attachments": [
            {"name": "empty.md", "content": ""},
            {"name": "notes.md", "content": "Notes"},
        ],
    }

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _index: entrypoints.table_say(**request), range(8)))

        assert all(isinstance(result, Success) for result in results)
        responses = [result.unwrap()["data"] for result in results]
        assert len({response["id"] for response in responses}) == 1
        assert {response["sequence"] for response in responses} == {0}
        assert all(
            [item["name"] for item in response["attachments"]] == ["empty.md", "notes.md"]
            for response in responses
        )

        verify_conn = sqlite3.connect(str(db_path))
        try:
            assert verify_conn.execute("SELECT COUNT(*) FROM sayings").fetchone()[0] == 1
            assert verify_conn.execute(
                "SELECT COUNT(*) FROM saying_attachments"
            ).fetchone()[0] == 2
            assert verify_conn.execute(
                "SELECT COUNT(*) FROM idempotency_keys WHERE tool_name = 'table_say'"
            ).fetchone()[0] == 1
            assert verify_conn.execute("SELECT MAX(sequence) FROM sayings").fetchone()[0] == 0
        finally:
            verify_conn.close()
    finally:
        for conn in opened_connections:
            conn.close()
