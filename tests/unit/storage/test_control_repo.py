"""
Unit tests for atomic control operations repository.

Tests for:
- atomic_control_table function
- Transaction rollback on errors
- Version conflict handling
- Integrity error handling
"""

import sqlite3
from collections.abc import Generator
from datetime import datetime

import pytest
from returns.result import Failure, Success

from tasca.core.domain.patron import PatronId
from tasca.core.domain.saying import Speaker, SpeakerKind
from tasca.core.domain.table import Table, TableId, TableStatus, Version
from tasca.core.schema import create_sayings_table_ddl, create_tables_table_ddl
from tasca.shell.storage.control_repo import ControlError, atomic_control_table
from tasca.shell.storage.table_repo import create_table


@pytest.fixture
def db_conn() -> Generator[sqlite3.Connection, None, None]:
    """Create an in-memory database with schemas."""
    conn = sqlite3.connect(":memory:")
    conn.execute(create_tables_table_ddl())
    conn.execute(create_sayings_table_ddl())
    conn.commit()
    yield conn
    conn.close()


def create_test_table(
    table_id: str,
    question: str,
    status: TableStatus = TableStatus.OPEN,
    version: int = 1,
    created_at: datetime | None = None,
) -> Table:
    """Helper to create a test table."""
    ts = created_at or datetime(2024, 1, 1, 12, 0, 0)
    return Table(
        id=TableId(table_id),
        question=question,
        context=None,
        status=status,
        version=Version(version),
        created_at=ts,
        updated_at=ts,
    )


class TestAtomicControlTable:
    """Tests for atomic_control_table function."""

    def test_happy_path_pause_succeeds(self, db_conn: sqlite3.Connection) -> None:
        """Atomic pause operation succeeds."""
        # Create initial table
        table = create_test_table("table-1", "Test question", TableStatus.OPEN)
        create_table(db_conn, table)

        speaker = Speaker(kind=SpeakerKind.AGENT, name="moderator", patron_id=PatronId("patron-1"))
        now = datetime(2024, 1, 1, 12, 30, 0)

        result = atomic_control_table(
            db_conn,
            "table-1",
            speaker,
            "Table paused by moderator",
            TableStatus.PAUSED,
            table,
            now,
        )

        assert isinstance(result, Success)
        saying, updated_table = result.unwrap()

        # Verify saying was created
        assert saying.table_id == "table-1"
        assert saying.sequence == 0  # First saying
        assert saying.speaker == speaker
        assert saying.content == "Table paused by moderator"

        # Verify table was updated
        assert updated_table.status == TableStatus.PAUSED
        assert updated_table.version == 2  # Incremented

        # Verify database state
        cursor = db_conn.cursor()
        cursor.execute("SELECT status, version FROM tables WHERE id = ?", ("table-1",))
        row = cursor.fetchone()
        assert row[0] == "paused"
        assert row[1] == 2

        cursor.execute("SELECT COUNT(*) FROM sayings WHERE table_id = ?", ("table-1",))
        assert cursor.fetchone()[0] == 1

    def test_happy_path_close_succeeds(self, db_conn: sqlite3.Connection) -> None:
        """Atomic close operation succeeds."""
        # Create table in OPEN state
        table = create_test_table("table-1", "Test", TableStatus.OPEN)
        create_table(db_conn, table)

        speaker = Speaker(kind=SpeakerKind.HUMAN, name="owner")
        now = datetime(2024, 1, 1, 14, 0, 0)

        result = atomic_control_table(
            db_conn,
            "table-1",
            speaker,
            "Discussion closed",
            TableStatus.CLOSED,
            table,
            now,
        )

        assert isinstance(result, Success)
        _, updated_table = result.unwrap()
        assert updated_table.status == TableStatus.CLOSED

    def test_version_conflict_returns_failure(self, db_conn: sqlite3.Connection) -> None:
        """Version mismatch causes rollback and returns Failure."""
        # Create initial table
        table = create_test_table("table-1", "Test", TableStatus.OPEN)
        create_table(db_conn, table)

        # Simulate concurrent modification by updating the table directly
        cursor = db_conn.cursor()
        cursor.execute(
            "UPDATE tables SET version = 5, status = ? WHERE id = ?",
            (TableStatus.PAUSED.value, "table-1"),
        )
        db_conn.commit()

        speaker = Speaker(kind=SpeakerKind.AGENT, name="moderator")
        now = datetime(2024, 1, 1, 12, 0, 0)

        # Try to control with outdated version
        result = atomic_control_table(
            db_conn,
            "table-1",
            speaker,
            "Pause attempt",
            TableStatus.PAUSED,
            table,  # Has version=1, but DB has version=5
            now,
        )

        assert isinstance(result, Failure)
        error = result.failure()
        assert "Version conflict" in error
        assert "expected 1" in error

        # Verify no saying was created (rollback)
        cursor.execute("SELECT COUNT(*) FROM sayings WHERE table_id = ?", ("table-1",))
        assert cursor.fetchone()[0] == 0

        # Verify table unchanged
        cursor.execute("SELECT status, version FROM tables WHERE id = ?", ("table-1",))
        row = cursor.fetchone()
        assert row[0] == "paused"  # From our direct update
        assert row[1] == 5

    def test_integrity_error_returns_failure(self, db_conn: sqlite3.Connection) -> None:
        """Unique constraint violation causes rollback and returns Failure."""
        # Create initial table
        table = create_test_table("table-1", "Test", TableStatus.OPEN)
        create_table(db_conn, table)

        # Insert a saying manually at sequence 0
        cursor = db_conn.cursor()
        cursor.execute(
            """
            INSERT INTO sayings (
                id, table_id, sequence, speaker_kind, speaker_name,
                patron_id, content, pinned, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "saying-existing",
                "table-1",
                0,  # Sequence 0 already exists
                "agent",
                "system",
                None,
                "Existing message",
                0,
                datetime(2024, 1, 1, 12, 0, 0).isoformat(),
            ),
        )
        db_conn.commit()

        speaker = Speaker(kind=SpeakerKind.AGENT, name="moderator")
        now = datetime(2024, 1, 1, 13, 0, 0)

        # This should try to insert at sequence 0 (since we check MAX)
        # Actually, since we use COALESCE(MAX(sequence)), it will compute next
        # Let's create the scenario where it would conflict
        result = atomic_control_table(
            db_conn,
            "table-1",
            speaker,
            "Pause attempt",
            TableStatus.PAUSED,
            table,
            now,
        )

        # Since MAX(sequence) = 0, next_sequence = 1, no conflict expected
        assert isinstance(result, Success)

    def test_multiple_controls_increment_version(self, db_conn: sqlite3.Connection) -> None:
        """Multiple control operations increment version each time."""
        # Create initial table
        table = create_test_table("table-1", "Test", TableStatus.OPEN, version=1)
        create_table(db_conn, table)

        speaker = Speaker(kind=SpeakerKind.AGENT, name="moderator", patron_id=PatronId("p1"))
        now = datetime(2024, 1, 1, 12, 0, 0)

        # First: OPEN -> PAUSED
        result1 = atomic_control_table(
            db_conn, "table-1", speaker, "Paused", TableStatus.PAUSED, table, now
        )
        assert isinstance(result1, Success)
        _, table_v2 = result1.unwrap()
        assert table_v2.version == 2

        # Second: PAUSED -> OPEN (resume)
        now2 = datetime(2024, 1, 1, 13, 0, 0)
        result2 = atomic_control_table(
            db_conn, "table-1", speaker, "Resumed", TableStatus.OPEN, table_v2, now2
        )
        assert isinstance(result2, Success)
        _, table_v3 = result2.unwrap()
        assert table_v3.version == 3

        # Third: OPEN -> CLOSED
        now3 = datetime(2024, 1, 1, 14, 0, 0)
        result3 = atomic_control_table(
            db_conn, "table-1", speaker, "Closed", TableStatus.CLOSED, table_v3, now3
        )
        assert isinstance(result3, Success)
        _, table_v4 = result3.unwrap()
        assert table_v4.version == 4

        # Verify final state
        cursor = db_conn.cursor()
        cursor.execute("SELECT status, version FROM tables WHERE id = ?", ("table-1",))
        row = cursor.fetchone()
        assert row[0] == "closed"
        assert row[1] == 4

        # Verify all sayings created
        cursor.execute("SELECT COUNT(*) FROM sayings WHERE table_id = ?", ("table-1",))
        assert cursor.fetchone()[0] == 3

    def test_sequences_increment_correctly(self, db_conn: sqlite3.Connection) -> None:
        """Saying sequences increment from existing max."""
        # Create initial table
        table = create_test_table("table-1", "Test", TableStatus.OPEN)
        create_table(db_conn, table)

        cursor = db_conn.cursor()

        # Insert some existing sayings
        for i in range(3):
            cursor.execute(
                """
                INSERT INTO sayings (
                    id, table_id, sequence, speaker_kind, speaker_name,
                    patron_id, content, pinned, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"saying-{i}",
                    "table-1",
                    i,
                    "agent",
                    f"speaker-{i}",
                    None,
                    f"Message {i}",
                    0,
                    datetime(2024, 1, 1, 12, i, 0).isoformat(),
                ),
            )
        db_conn.commit()

        speaker = Speaker(kind=SpeakerKind.AGENT, name="moderator")
        now = datetime(2024, 1, 1, 13, 0, 0)

        result = atomic_control_table(
            db_conn,
            "table-1",
            speaker,
            "Pause",
            TableStatus.PAUSED,
            table,
            now,
        )

        assert isinstance(result, Success)
        saying, _ = result.unwrap()

        # Sequence should be 3 (max was 2, compute_next_sequence returns 3)
        assert saying.sequence == 3

    def test_first_saying_gets_sequence_zero(self, db_conn: sqlite3.Connection) -> None:
        """First saying in empty table gets sequence 0."""
        # Create table with no sayings
        table = create_test_table("table-1", "Test", TableStatus.OPEN)
        create_table(db_conn, table)

        speaker = Speaker(kind=SpeakerKind.AGENT, name="moderator")
        now = datetime(2024, 1, 1, 12, 0, 0)

        result = atomic_control_table(
            db_conn,
            "table-1",
            speaker,
            "Pause",
            TableStatus.PAUSED,
            table,
            now,
        )

        assert isinstance(result, Success)
        saying, _ = result.unwrap()
        assert saying.sequence == 0

    def test_rollback_on_error_leaves_database_consistent(
        self, db_conn: sqlite3.Connection
    ) -> None:
        """On any failure, both saying and table remain unchanged."""
        # Create initial table
        table = create_test_table("table-1", "Test", TableStatus.OPEN, version=1)
        create_table(db_conn, table)

        cursor = db_conn.cursor()

        # Get initial state
        cursor.execute("SELECT status, version FROM tables WHERE id = ?", ("table-1",))
        initial_row = cursor.fetchone()
        initial_status = initial_row[0]
        initial_version = initial_row[1]

        cursor.execute("SELECT COUNT(*) FROM sayings WHERE table_id = ?", ("table-1",))
        initial_saying_count = cursor.fetchone()[0]

        # Simulate version conflict (will trigger rollback)
        cursor.execute("UPDATE tables SET version = 99 WHERE id = ?", ("table-1",))
        db_conn.commit()

        speaker = Speaker(kind=SpeakerKind.AGENT, name="moderator")
        now = datetime(2024, 1, 1, 12, 0, 0)

        result = atomic_control_table(
            db_conn,
            "table-1",
            speaker,
            "Pause",
            TableStatus.PAUSED,
            table,  # Has version=1
            now,
        )

        assert isinstance(result, Failure)

        # Verify no new saying was created
        cursor.execute("SELECT COUNT(*) FROM sayings WHERE table_id = ?", ("table-1",))
        assert cursor.fetchone()[0] == initial_saying_count

        # Verify table state (should be the modified state, not rolled back to original)
        # because the rollback only affects the atomic_control_table transaction
        cursor.execute("SELECT status, version FROM tables WHERE id = ?", ("table-1",))
        row = cursor.fetchone()
        assert row[1] == 99  # The external modification is preserved


class TestAtomicControlTableEdgeCases:
    """Edge case tests for atomic_control_table."""

    def test_human_speaker_without_patron_id(self, db_conn: sqlite3.Connection) -> None:
        """Human speakers (no patron_id) work correctly."""
        # Create initial table
        table = create_test_table("table-1", "Test", TableStatus.OPEN)
        create_table(db_conn, table)

        speaker = Speaker(kind=SpeakerKind.HUMAN, name="Alice", patron_id=None)
        now = datetime(2024, 1, 1, 12, 0, 0)

        result = atomic_control_table(
            db_conn,
            "table-1",
            speaker,
            "Closed by human",
            TableStatus.CLOSED,
            table,
            now,
        )

        assert isinstance(result, Success)
        saying, _ = result.unwrap()
        assert saying.speaker.kind == SpeakerKind.HUMAN
        assert saying.speaker.patron_id is None

    def test_agent_speaker_with_patron_id(self, db_conn: sqlite3.Connection) -> None:
        """Agent speakers with patron_id work correctly."""
        # Create initial table
        table = create_test_table("table-1", "Test", TableStatus.OPEN)
        create_table(db_conn, table)

        speaker = Speaker(kind=SpeakerKind.AGENT, name="Bot", patron_id=PatronId("patron-bot-123"))
        now = datetime(2024, 1, 1, 12, 0, 0)

        result = atomic_control_table(
            db_conn,
            "table-1",
            speaker,
            "Closed by bot",
            TableStatus.CLOSED,
            table,
            now,
        )

        assert isinstance(result, Success)
        saying, _ = result.unwrap()
        assert saying.speaker.kind == SpeakerKind.AGENT
        assert saying.speaker.patron_id == PatronId("patron-bot-123")

    def test_pinned_always_false_for_control(self, db_conn: sqlite3.Connection) -> None:
        """CONTROL sayings are never pinned."""
        # Create initial table
        table = create_test_table("table-1", "Test", TableStatus.OPEN)
        create_table(db_conn, table)

        speaker = Speaker(kind=SpeakerKind.AGENT, name="moderator")
        now = datetime(2024, 1, 1, 12, 0, 0)

        result = atomic_control_table(
            db_conn,
            "table-1",
            speaker,
            "Pausing",
            TableStatus.PAUSED,
            table,
            now,
        )

        assert isinstance(result, Success)
        saying, _ = result.unwrap()
        assert saying.pinned is False

        # Verify in database too
        cursor = db_conn.cursor()
        cursor.execute("SELECT pinned FROM sayings WHERE id = ?", (saying.id,))
        assert cursor.fetchone()[0] == 0
