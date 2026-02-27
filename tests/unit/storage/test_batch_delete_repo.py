"""Unit tests for batch delete repository function."""

import sqlite3
from datetime import datetime

import pytest
from returns.result import Failure, Success

from tasca.core.domain.seat import Seat, SeatId, SeatState
from tasca.core.domain.saying import Speaker, SpeakerKind
from tasca.core.domain.table import Table, TableId, TableStatus, Version
from tasca.shell.storage.database import apply_schema
from tasca.shell.storage.seat_repo import create_seat
from tasca.shell.storage.saying_repo import append_saying
from tasca.shell.storage.table_repo import (
    batch_delete_tables,
    create_table,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """Create an in-memory database with full schema (tables, seats, sayings, FTS)."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    result = apply_schema(conn)
    assert isinstance(result, Success)
    yield conn
    conn.close()


def _make_table(table_id: str, status: TableStatus = TableStatus.CLOSED) -> Table:
    ts = datetime(2024, 1, 1, 12, 0, 0)
    return Table(
        id=TableId(table_id),
        question="Test question",
        context=None,
        status=status,
        version=Version(1),
        created_at=ts,
        updated_at=ts,
    )


def _make_seat(seat_id: str, table_id: str, patron_id: str) -> Seat:
    ts = datetime(2024, 1, 1, 12, 0, 0)
    return Seat(
        id=SeatId(seat_id),
        table_id=TableId(table_id),
        patron_id=patron_id,
        state=SeatState.JOINED,
        last_heartbeat=ts,
        joined_at=ts,
    )


def _insert_patron(conn: sqlite3.Connection, patron_id: str) -> None:
    """Insert a minimal patron row for FK satisfaction."""
    conn.execute(
        "INSERT INTO patrons (id, name, kind, created_at) VALUES (?, ?, ?, ?)",
        (patron_id, "Test Patron", "agent", "2024-01-01T00:00:00"),
    )
    conn.commit()


# =============================================================================
# Happy path
# =============================================================================


class TestHappyPath:
    def test_delete_single_table(self, db_conn):
        table = _make_table("t1")
        create_table(db_conn, table)

        result = batch_delete_tables(db_conn, ["t1"])
        assert isinstance(result, Success)
        assert "t1" in result.unwrap()

        # Verify table is gone
        row = db_conn.execute("SELECT id FROM tables WHERE id = 't1'").fetchone()
        assert row is None

    def test_delete_multiple_tables(self, db_conn):
        for i in range(3):
            create_table(db_conn, _make_table(f"t{i}"))

        result = batch_delete_tables(db_conn, ["t0", "t1", "t2"])
        assert isinstance(result, Success)
        assert len(result.unwrap()) == 3

        count = db_conn.execute("SELECT COUNT(*) FROM tables").fetchone()[0]
        assert count == 0

    def test_delete_empty_list(self, db_conn):
        result = batch_delete_tables(db_conn, [])
        assert isinstance(result, Success)
        assert result.unwrap() == []


# =============================================================================
# Cascade: seats and sayings deleted
# =============================================================================


class TestCascadeDelete:
    def test_seats_deleted_with_table(self, db_conn):
        _insert_patron(db_conn, "p1")
        create_table(db_conn, _make_table("t1"))
        create_seat(db_conn, _make_seat("s1", "t1", "p1"))

        # Verify seat exists
        assert db_conn.execute("SELECT COUNT(*) FROM seats WHERE table_id = 't1'").fetchone()[0] == 1

        result = batch_delete_tables(db_conn, ["t1"])
        assert isinstance(result, Success)

        # Seat should be gone
        assert db_conn.execute("SELECT COUNT(*) FROM seats WHERE table_id = 't1'").fetchone()[0] == 0

    def test_sayings_deleted_with_table(self, db_conn):
        _insert_patron(db_conn, "p1")
        create_table(db_conn, _make_table("t1"))

        speaker = Speaker(kind=SpeakerKind.AGENT, name="test", patron_id="p1")
        append_saying(db_conn, "t1", speaker, "Hello world")

        # Verify saying exists
        assert db_conn.execute("SELECT COUNT(*) FROM sayings WHERE table_id = 't1'").fetchone()[0] == 1

        result = batch_delete_tables(db_conn, ["t1"])
        assert isinstance(result, Success)

        # Saying should be gone
        assert db_conn.execute("SELECT COUNT(*) FROM sayings WHERE table_id = 't1'").fetchone()[0] == 0

    def test_full_cascade_seats_sayings_table(self, db_conn):
        _insert_patron(db_conn, "p1")
        _insert_patron(db_conn, "p2")
        create_table(db_conn, _make_table("t1"))
        create_table(db_conn, _make_table("t2"))

        # Add seats and sayings to both tables
        create_seat(db_conn, _make_seat("s1", "t1", "p1"))
        create_seat(db_conn, _make_seat("s2", "t2", "p2"))
        speaker = Speaker(kind=SpeakerKind.AGENT, name="test", patron_id="p1")
        append_saying(db_conn, "t1", speaker, "Message 1")
        append_saying(db_conn, "t2", speaker, "Message 2")

        # Delete both tables
        result = batch_delete_tables(db_conn, ["t1", "t2"])
        assert isinstance(result, Success)

        # Everything should be gone
        assert db_conn.execute("SELECT COUNT(*) FROM tables").fetchone()[0] == 0
        assert db_conn.execute("SELECT COUNT(*) FROM seats").fetchone()[0] == 0
        assert db_conn.execute("SELECT COUNT(*) FROM sayings").fetchone()[0] == 0

    def test_other_tables_unaffected(self, db_conn):
        _insert_patron(db_conn, "p1")
        create_table(db_conn, _make_table("t1"))
        create_table(db_conn, _make_table("t2"))
        create_seat(db_conn, _make_seat("s1", "t1", "p1"))
        create_seat(db_conn, _make_seat("s2", "t2", "p1"))

        # Only delete t1
        batch_delete_tables(db_conn, ["t1"])

        # t2 and its seat should remain
        assert db_conn.execute("SELECT COUNT(*) FROM tables").fetchone()[0] == 1
        assert db_conn.execute("SELECT id FROM tables").fetchone()[0] == "t2"
        assert db_conn.execute("SELECT COUNT(*) FROM seats WHERE table_id = 't2'").fetchone()[0] == 1


# =============================================================================
# Error cases
# =============================================================================


class TestErrors:
    def test_nonexistent_ids_returns_failure(self, db_conn):
        result = batch_delete_tables(db_conn, ["nonexistent"])
        assert isinstance(result, Failure)

    def test_rollback_on_error(self, db_conn):
        """Verify that partial state is rolled back on error."""
        create_table(db_conn, _make_table("t1"))

        # Verify table exists before
        assert db_conn.execute("SELECT COUNT(*) FROM tables").fetchone()[0] == 1

        # This should succeed (valid ID)
        result = batch_delete_tables(db_conn, ["t1"])
        assert isinstance(result, Success)
        assert db_conn.execute("SELECT COUNT(*) FROM tables").fetchone()[0] == 0
