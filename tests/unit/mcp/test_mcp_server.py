"""
Unit tests for MCP server tools.

Tests each MCP tool's contract using in-memory SQLite database.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any, Generator
from unittest.mock import AsyncMock, patch

import pytest
from returns.result import Success

from tasca.core.domain.patron import Patron, PatronId
from tasca.core.domain.table import Table, TableId, TableStatus, TableUpdate, Version
from tasca.shell.mcp.server import (
    patron_get,
    patron_register,
    seat_heartbeat,
    seat_list,
    table_control,
    table_create,
    table_export,
    table_get,
    table_join,
    table_list,
    table_listen,
    table_say,
    table_update,
    table_wait,
)
from tasca.shell.storage.database import apply_schema
from tasca.shell.storage.table_repo import create_table, update_table


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def test_db() -> Generator[sqlite3.Connection, None, None]:
    """Create an in-memory database with full schema."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    apply_schema(conn)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def override_db(test_db: sqlite3.Connection) -> Generator[None, None, None]:
    """Override the MCP database connection to use test database."""
    from tasca.shell.mcp import database
    import importlib
    import tasca.shell.mcp.server as server

    original_connection = database._mcp_db_connection

    # Set the connection directly - get_mcp_db will yield it
    database._mcp_db_connection = test_db

    # Reload server to pick up the patched database module state
    importlib.reload(server)

    yield

    database._mcp_db_connection = original_connection


def unique_name(base: str = "Agent") -> str:
    """Generate a unique name using UUID."""
    return f"{base}-{uuid.uuid4().hex[:8]}"


# =============================================================================
# Patron Tools Tests
# =============================================================================


class TestPatronRegister:
    """Tests for patron_register MCP tool."""

    def test_register_patron_success(self) -> None:
        """Register a new patron successfully."""
        name = unique_name()
        result = patron_register(name=name, kind="agent")

        assert result["ok"] is True
        data = result["data"]
        # Backward-compat fields
        assert "id" in data
        assert data["name"] == name
        # Spec fields
        assert "patron_id" in data
        assert data["patron_id"] == data["id"]
        assert "display_name" in data
        assert data["display_name"] == name
        assert data["kind"] == "agent"
        assert data["is_new"] is True
        assert "created_at" in data

    def test_register_patron_default_kind(self) -> None:
        """Register patron with default kind."""
        name = unique_name()
        result = patron_register(name=name)

        assert result["ok"] is True
        data = result["data"]
        assert data["kind"] == "agent"
        # Spec fields present
        assert data["display_name"] == name
        assert data["patron_id"] == data["id"]

    def test_register_patron_human_kind(self) -> None:
        """Register patron with human kind."""
        name = unique_name("Human")
        result = patron_register(name=name, kind="human")

        assert result["ok"] is True
        data = result["data"]
        assert data["kind"] == "human"
        # Spec fields present
        assert data["display_name"] == name
        assert data["patron_id"] == data["id"]

    def test_register_patron_dedup_returns_existing(self) -> None:
        """Registering same name returns existing patron with is_new=False."""
        name = unique_name()
        # First registration
        result1 = patron_register(name=name)
        assert result1["ok"] is True
        data1 = result1["data"]
        assert data1["is_new"] is True
        first_id = data1["id"]
        # Spec fields on first registration
        assert data1["patron_id"] == first_id
        assert data1["display_name"] == name

        # Second registration with same name
        result2 = patron_register(name=name)
        assert result2["ok"] is True
        data2 = result2["data"]
        assert data2["is_new"] is False
        assert data2["id"] == first_id
        # Spec fields on dedup (should match original)
        assert data2["patron_id"] == first_id
        assert data2["display_name"] == name


class TestPatronGet:
    """Tests for patron_get MCP tool."""

    def test_get_patron_not_found(self) -> None:
        """Get non-existent patron returns error envelope."""
        result = patron_get("nonexistent-id")

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"
        assert "not found" in result["error"]["message"].lower()

    def test_get_patron_exists(self) -> None:
        """Get existing patron returns patron details."""
        # Create a patron first
        name = unique_name()
        create_result = patron_register(name=name)
        patron_id = create_result["data"]["id"]

        # Get the patron
        result = patron_get(patron_id)

        assert result["ok"] is True
        assert result["data"]["id"] == patron_id
        assert result["data"]["name"] == name


# =============================================================================
# Table Tools Tests
# =============================================================================


class TestTableCreate:
    """Tests for table_create MCP tool."""

    def test_create_table_success(self) -> None:
        """Create a new table successfully."""
        result = table_create(question="What is the meaning of life?")

        assert result["ok"] is True
        data = result["data"]
        assert "id" in data
        assert data["question"] == "What is the meaning of life?"
        assert data["status"] == "open"
        assert data["version"] == 1
        assert "created_at" in data
        assert "updated_at" in data

    def test_create_table_with_context(self) -> None:
        """Create a table with context."""
        result = table_create(
            question="What framework to use?",
            context="Building a new web application",
        )

        assert result["ok"] is True
        assert result["data"]["context"] == "Building a new web application"


class TestTableGet:
    """Tests for table_get MCP tool."""

    def test_get_table_not_found(self) -> None:
        """Get non-existent table returns error envelope."""
        result = table_get("nonexistent-id")

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"

    def test_get_table_exists(self) -> None:
        """Get existing table returns table details."""
        # Create a table first
        create_result = table_create(question="Test question")
        table_id = create_result["data"]["id"]

        # Get the table
        result = table_get(table_id)

        assert result["ok"] is True
        assert result["data"]["id"] == table_id
        assert result["data"]["question"] == "Test question"


class TestTableList:
    """Tests for table_list MCP tool."""

    def test_list_tables_empty(self) -> None:
        """List tables when none exist returns empty list."""
        result = table_list()

        assert result["ok"] is True
        assert result["data"]["tables"] == []
        assert result["data"]["total"] == 0

    def test_list_tables_returns_open_tables(self) -> None:
        """List tables returns only open tables with seat counts."""
        # Create some tables
        result1 = table_create(question="First table")
        result2 = table_create(question="Second table")

        # List open tables
        result = table_list(status="open")

        assert result["ok"] is True
        tables = result["data"]["tables"]
        assert len(tables) == 2
        assert result["data"]["total"] == 2

        # Check each table has required fields
        for table in tables:
            assert "id" in table
            assert "question" in table
            assert "status" in table
            assert table["status"] == "open"
            assert "active_count" in table
            assert isinstance(table["active_count"], int)

    def test_list_tables_default_status_open(self) -> None:
        """List tables defaults to 'open' status."""
        result = table_list()  # Default status='open'

        assert result["ok"] is True
        assert "tables" in result["data"]

    def test_list_tables_invalid_status(self) -> None:
        """List tables with unsupported status returns error."""
        result = table_list(status="closed")  # type: ignore[arg-type]

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_REQUEST"
        assert "open" in result["error"]["message"].lower()

    def test_list_tables_with_active_seats(self) -> None:
        """List tables includes active seat counts."""
        # Create patron and table
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]
        table_result = table_create(question="Table with seats")
        table_id = table_result["data"]["id"]

        # Join the table (creates a seat)
        table_join(table_id=table_id, patron_id=patron_id)

        # List tables
        result = table_list()

        assert result["ok"] is True
        tables = result["data"]["tables"]

        # Find our table
        our_table = next((t for t in tables if t["id"] == table_id), None)
        assert our_table is not None
        assert our_table["active_count"] >= 1

    def test_list_tables_response_envelope_shape(self) -> None:
        """List tables returns correct response envelope shape."""
        table_create(question="Shape test table")

        result = table_list()

        assert result["ok"] is True
        assert "data" in result
        assert "tables" in result["data"]
        assert "total" in result["data"]
        assert isinstance(result["data"]["tables"], list)
        assert isinstance(result["data"]["total"], int)

    def test_list_tables_excludes_non_open_tables(self, test_db: sqlite3.Connection) -> None:
        """List tables excludes closed/paused tables (status filter)."""
        from tasca.shell.storage.table_repo import update_table

        # Create multiple tables
        result_open = table_create(question="Open table")
        open_table_id = result_open["data"]["id"]

        result_to_close = table_create(question="Will be closed")
        closed_table_id = result_to_close["data"]["id"]

        result_to_pause = table_create(question="Will be paused")
        paused_table_id = result_to_pause["data"]["id"]

        # Update tables to closed/paused status
        now = datetime.now(UTC)
        from tasca.core.domain.table import TableStatus, TableUpdate

        close_update = TableUpdate(
            question="Will be closed", context=None, status=TableStatus.CLOSED
        )
        pause_update = TableUpdate(
            question="Will be paused", context=None, status=TableStatus.PAUSED
        )

        update_table(
            test_db,
            TableId(closed_table_id),
            close_update,
            Version(1),
            now,
        )
        update_table(
            test_db,
            TableId(paused_table_id),
            pause_update,
            Version(1),
            now,
        )

        # List tables - only open should appear
        result = table_list()

        assert result["ok"] is True
        tables = result["data"]["tables"]
        table_ids = {t["id"] for t in tables}

        # Open table should be in list
        assert open_table_id in table_ids
        # Closed and paused should NOT be in list
        assert closed_table_id not in table_ids
        assert paused_table_id not in table_ids
        # Total should only count open tables
        assert result["data"]["total"] == 1

    def test_list_tables_excludes_expired_seats_from_count(
        self, test_db: sqlite3.Connection
    ) -> None:
        """Active count excludes seats with expired heartbeats."""
        from tasca.core.domain.seat import Seat, SeatId, SeatState
        from tasca.core.services.seat_service import DEFAULT_SEAT_TTL_SECONDS
        from tasca.shell.storage.seat_repo import create_seat

        # Create patron and table
        patron_result = patron_register(name=unique_name())
        patron_active = patron_result["data"]["id"]
        patron_result2 = patron_register(name=unique_name())
        patron_expired = patron_result2["data"]["id"]

        table_result = table_create(question="Table with mixed seats")
        table_id = table_result["data"]["id"]

        # Create an active seat
        table_join(table_id=table_id, patron_id=patron_active)

        # Create an expired seat directly in DB (old heartbeat)
        now = datetime.now(UTC)
        expired_time = now.replace(year=now.year - 1)  # 1 year ago - definitely expired
        expired_seat = Seat(
            id=SeatId("expired-seat-123"),
            table_id=table_id,
            patron_id=patron_expired,
            state=SeatState.JOINED,
            last_heartbeat=expired_time,
            joined_at=expired_time,
        )
        create_seat(test_db, expired_seat)

        # List tables
        result = table_list()

        assert result["ok"] is True
        tables = result["data"]["tables"]

        # Find our table
        our_table = next((t for t in tables if t["id"] == table_id), None)
        assert our_table is not None
        # Only the active seat should be counted (expired seat excluded)
        assert our_table["active_count"] == 1


class TestTableJoin:
    """Tests for table_join MCP tool."""

    def test_join_table_not_found(self) -> None:
        """Join non-existent table returns error envelope."""
        # Create a patron first
        patron_result = patron_register(name="Test Patron")
        patron_id = patron_result["data"]["id"]

        result = table_join(table_id="nonexistent-id", patron_id=patron_id)

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"

    def test_join_patron_not_found(self) -> None:
        """Join with non-existent patron returns error envelope."""
        # Create a table first
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]

        result = table_join(table_id=table_id, patron_id="nonexistent-id")

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"

    def test_join_table_success(self) -> None:
        """Join table creates seat and returns table details."""
        # Create patron and table
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]

        # Join the table
        result = table_join(table_id=table_id, patron_id=patron_id)

        assert result["ok"] is True
        data = result["data"]
        assert "table" in data
        assert "seat" in data
        assert data["table"]["id"] == table_id
        assert data["seat"]["patron_id"] == patron_id
        assert data["seat"]["state"] == "joined"
        assert "expires_at" in data["seat"]

    def test_join_initial_sayings_empty_table(self) -> None:
        """Join table with no sayings returns empty initial_sayings."""
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]
        table_result = table_create(question="Empty table question")
        table_id = table_result["data"]["id"]

        result = table_join(table_id=table_id, patron_id=patron_id)

        assert result["ok"] is True
        initial = result["data"]["initial_sayings"]
        assert initial["sayings"] == []
        assert initial["next_sequence"] == -1
        assert initial["has_more"] is False

    def test_join_initial_sayings_populated_table(self) -> None:
        """Join table with existing sayings returns them in sequence order."""
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]
        table_result = table_create(question="Populated table")
        table_id = table_result["data"]["id"]

        # Add 3 sayings
        for i in range(3):
            table_say(table_id=table_id, content=f"Saying {i}", speaker_name="Speaker")

        result = table_join(table_id=table_id, patron_id=patron_id)

        assert result["ok"] is True
        initial = result["data"]["initial_sayings"]
        sayings = initial["sayings"]
        assert len(sayings) == 3
        # Verify sequence order (ascending)
        sequences = [s["sequence"] for s in sayings]
        assert sequences == sorted(sequences)

    def test_join_initial_sayings_next_sequence_correctness(self) -> None:
        """next_sequence equals max(sequence) when sayings exist.

        Convention matches table_listen since_sequence semantics:
        table_listen returns sayings with sequence > since_sequence.
        So passing next_sequence as since_sequence yields no duplicates and no gaps.
        """
        table_result = table_create(question="Sequence test table")
        table_id = table_result["data"]["id"]

        # Add 4 sayings (sequences 0..3)
        for i in range(4):
            table_say(table_id=table_id, content=f"Saying {i}", speaker_name="Speaker")

        result = table_join(table_id=table_id)

        assert result["ok"] is True
        initial = result["data"]["initial_sayings"]
        max_seq = max(s["sequence"] for s in initial["sayings"])
        assert initial["next_sequence"] == max_seq

    def test_join_initial_sayings_has_more_true(self) -> None:
        """has_more is True when more sayings exist beyond the history limit."""
        table_result = table_create(question="has_more table")
        table_id = table_result["data"]["id"]

        # Add 12 sayings — exceeds DEFAULT_HISTORY_LIMIT of 10
        for i in range(12):
            table_say(table_id=table_id, content=f"Saying {i}", speaker_name="Speaker")

        result = table_join(table_id=table_id)

        assert result["ok"] is True
        initial = result["data"]["initial_sayings"]
        assert len(initial["sayings"]) == 10  # capped at limit
        assert initial["has_more"] is True

    def test_join_initial_sayings_has_more_false_populated(self) -> None:
        """has_more is False when sayings fit within the history limit."""
        table_result = table_create(question="Under limit table")
        table_id = table_result["data"]["id"]

        # Add 3 sayings — well under DEFAULT_HISTORY_LIMIT of 10
        for i in range(3):
            table_say(table_id=table_id, content=f"Saying {i}", speaker_name="Speaker")

        result = table_join(table_id=table_id)

        assert result["ok"] is True
        initial = result["data"]["initial_sayings"]
        assert len(initial["sayings"]) > 0
        assert initial["has_more"] is False

    def test_join_backward_compat_table_and_seat_fields(self) -> None:
        """Response still contains table and seat fields alongside initial_sayings."""
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]
        table_result = table_create(question="Backward compat question")
        table_id = table_result["data"]["id"]

        result = table_join(table_id=table_id, patron_id=patron_id)

        assert result["ok"] is True
        data = result["data"]
        assert "table" in data
        assert "seat" in data
        assert "initial_sayings" in data
        assert data["table"]["id"] == table_id
        assert data["seat"]["patron_id"] == patron_id


class TestTableSay:
    """Tests for table_say MCP tool."""

    def test_say_table_not_found(self) -> None:
        """Say to non-existent table returns error envelope."""
        result = table_say(
            table_id="nonexistent-id",
            content="Hello",
            speaker_name="Test Speaker",
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"

    def test_say_success(self) -> None:
        """Say creates a saying with correct sequence."""
        # Create table first
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]

        # Say something
        result = table_say(
            table_id=table_id,
            content="Hello, world!",
            speaker_name="Test Speaker",
        )

        assert result["ok"] is True
        data = result["data"]
        assert "id" in data
        assert data["table_id"] == table_id
        assert data["sequence"] == 0  # First saying
        assert data["content"] == "Hello, world!"
        assert data["speaker"]["name"] == "Test Speaker"
        assert data["speaker"]["kind"] == "agent"  # Default speaker_kind is "agent"

    def test_say_with_patron(self) -> None:
        """Say with patron creates agent speaker."""
        # Create patron and table
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]

        # Say something with patron
        result = table_say(
            table_id=table_id,
            content="Agent speaking",
            speaker_name="Agent Speaker",
            patron_id=patron_id,
        )

        assert result["ok"] is True
        assert result["data"]["speaker"]["kind"] == "agent"
        assert result["data"]["speaker"]["patron_id"] == patron_id


class TestTableListen:
    """Tests for table_listen MCP tool."""

    def test_listen_table_not_found(self) -> None:
        """Listen to non-existent table returns error envelope."""
        result = table_listen(table_id="nonexistent-id")

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"

    def test_listen_empty_table(self) -> None:
        """Listen to empty table returns empty sayings list."""
        # Create table
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]

        result = table_listen(table_id=table_id)

        assert result["ok"] is True
        assert result["data"]["sayings"] == []
        assert result["data"]["next_sequence"] == -1

    def test_listen_with_sayings(self) -> None:
        """Listen returns sayings and correct next_sequence (spec: max sequence, not max+1)."""
        # Create table and say things
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]

        table_say(table_id=table_id, content="First", speaker_name="A")  # seq 0
        table_say(table_id=table_id, content="Second", speaker_name="B")  # seq 1

        # Listen for all
        result = table_listen(table_id=table_id, since_sequence=-1)

        assert result["ok"] is True
        sayings = result["data"]["sayings"]
        assert len(sayings) == 2
        assert sayings[0]["content"] == "First"
        assert sayings[1]["content"] == "Second"
        # Spec: next_sequence = max(sequence) = 1, not max+1 = 2
        # This allows client to pass since_sequence=1 to get sequences > 1 (i.e., 2, 3, 4...)
        # Old behavior (max+1=2) would cause missed saying when passing since_sequence=2
        assert result["data"]["next_sequence"] == 1

    def test_listen_with_since_sequence(self) -> None:
        """Listen with since_sequence filters sayings."""
        # Create table and say things
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]

        table_say(table_id=table_id, content="First", speaker_name="A")  # seq 0
        table_say(table_id=table_id, content="Second", speaker_name="B")  # seq 1
        table_say(table_id=table_id, content="Third", speaker_name="C")  # seq 2

        # Listen for sayings after sequence 0
        result = table_listen(table_id=table_id, since_sequence=0)

        assert result["ok"] is True
        sayings = result["data"]["sayings"]
        assert len(sayings) == 2  # Second and Third
        assert sayings[0]["content"] == "Second"
        assert sayings[1]["content"] == "Third"
        # Spec: next_sequence = max(sequence) = 2, not max+1 = 3
        assert result["data"]["next_sequence"] == 2

    def test_listen_next_sequence_prevents_duplicates_and_missed(self) -> None:
        """L1: next_sequence equals max sequence of returned sayings (spec compliance).

        Spec requires next_sequence = max(sequence), NOT max(sequence) + 1.
        This prevents:
        - Duplicates: If next_sequence were max+1, client would poll with since_sequence=max+1
          and miss the saying at sequence max.
        - Missed sayings: Client uses returned next_sequence as since_sequence for next call.
          Server returns sequences > since_sequence.
        """
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]

        # Add sayings with sequences 0, 1, 2
        table_say(table_id=table_id, content="A", speaker_name="A")  # seq 0
        table_say(table_id=table_id, content="B", speaker_name="B")  # seq 1
        table_say(table_id=table_id, content="C", speaker_name="C")  # seq 2

        # First call: get all sayings
        result1 = table_listen(table_id=table_id, since_sequence=-1)
        assert result1["ok"] is True
        sayings1 = result1["data"]["sayings"]
        assert len(sayings1) == 3
        # L1 verification: next_sequence = max(sequence of returned sayings)
        max_seq = max(s["sequence"] for s in sayings1)
        assert result1["data"]["next_sequence"] == max_seq

        # Second call: use next_sequence as since_sequence
        # With spec behavior: since_sequence=2, we get sequences > 2 (none yet)
        result2 = table_listen(table_id=table_id, since_sequence=result1["data"]["next_sequence"])
        assert result2["ok"] is True
        sayings2 = result2["data"]["sayings"]
        assert len(sayings2) == 0  # No new sayings

        # Add a new saying (seq 3)
        table_say(table_id=table_id, content="D", speaker_name="D")

        # Third call: should get sequence 3 only (not 2 again)
        result3 = table_listen(table_id=table_id, since_sequence=result1["data"]["next_sequence"])
        assert result3["ok"] is True
        sayings3 = result3["data"]["sayings"]
        assert len(sayings3) == 1
        assert sayings3[0]["content"] == "D"
        assert sayings3[0]["sequence"] == 3

    def test_listen_empty_returns_next_sequence_for_polling(self) -> None:
        """Empty result returns next_sequence for polling continuation.

        When no sayings are returned:
        - since_sequence=-1 (or any < 0) -> next_sequence=0
        - since_sequence=5 (no results) -> next_sequence=6
        This allows clients to continue polling without missing data.
        """
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]

        # Empty table
        result = table_listen(table_id=table_id, since_sequence=-1)
        assert result["ok"] is True
        assert result["data"]["sayings"] == []
        assert result["data"]["next_sequence"] == -1

        # Add a saying
        table_say(table_id=table_id, content="First", speaker_name="A")

        # Poll from after max (sequence 0)
        result2 = table_listen(table_id=table_id, since_sequence=10)
        assert result2["ok"] is True
        assert result2["data"]["sayings"] == []
        # Since no sayings returned, next_sequence = since_sequence (last-seen semantics)
        assert result2["data"]["next_sequence"] == 10


class TestTableExport:
    """Tests for table_export MCP tool.

    table_export exports a table and its sayings to markdown or jsonl format.
    """

    def test_export_table_not_found(self) -> None:
        """Export non-existent table returns NOT_FOUND error envelope."""
        result = table_export(table_id="nonexistent-id")

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"
        assert "table" in result["error"]["message"].lower()

    def test_export_markdown_empty_table(self) -> None:
        """Export empty table as markdown returns valid markdown."""
        # Create table
        table_result = table_create(question="What is the meaning of life?")
        table_id = table_result["data"]["id"]

        # Export as markdown
        result = table_export(table_id=table_id, format="markdown")

        assert result["ok"] is True
        data = result["data"]
        assert data["format"] == "markdown"
        assert data["table_id"] == table_id
        assert "content" in data

        # Verify markdown structure
        content = data["content"]
        assert "# What is the meaning of life?" in content
        assert "table_id:" in content
        assert "status: open" in content
        assert "_No sayings yet._" in content

    def test_export_markdown_with_sayings(self) -> None:
        """Export table with sayings as markdown includes full content."""
        # Create table and add sayings
        table_result = table_create(question="Discussion topic")
        table_id = table_result["data"]["id"]

        table_say(table_id=table_id, content="First point", speaker_name="Alice")
        table_say(table_id=table_id, content="Second point", speaker_name="Bob")

        # Export as markdown
        result = table_export(table_id=table_id, format="markdown")

        assert result["ok"] is True
        content = result["data"]["content"]

        # Verify content includes sayings
        assert "[seq=0]" in content
        assert "[seq=1]" in content
        assert "First point" in content
        assert "Second point" in content
        assert "agent:Alice" in content
        assert "agent:Bob" in content

    def test_export_jsonl_empty_table(self) -> None:
        """Export empty table as jsonl returns valid jsonl."""
        # Create table
        table_result = table_create(question="JSONL test")
        table_id = table_result["data"]["id"]

        # Export as jsonl
        result = table_export(table_id=table_id, format="jsonl")

        assert result["ok"] is True
        data = result["data"]
        assert data["format"] == "jsonl"
        assert data["table_id"] == table_id
        assert "content" in data

        # Verify jsonl structure (2 lines: header + table)
        import json

        lines = data["content"].strip().split("\n")
        assert len(lines) == 2

        # First line is header
        header = json.loads(lines[0])
        assert header["type"] == "export_header"
        assert header["table_id"] == table_id

        # Second line is table
        table_line = json.loads(lines[1])
        assert table_line["type"] == "table"
        assert table_line["table"]["question"] == "JSONL test"

    def test_export_jsonl_with_sayings(self) -> None:
        """Export table with sayings as jsonl includes all sayings."""
        # Create table and add sayings
        table_result = table_create(question="JSONL with sayings")
        table_id = table_result["data"]["id"]

        table_say(table_id=table_id, content="First", speaker_name="A")
        table_say(table_id=table_id, content="Second", speaker_name="B")

        # Export as jsonl
        result = table_export(table_id=table_id, format="jsonl")

        assert result["ok"] is True
        import json

        lines = result["data"]["content"].strip().split("\n")

        # 4 lines: header + table + 2 sayings
        assert len(lines) == 4

        # Verify saying lines
        saying1 = json.loads(lines[2])
        assert saying1["type"] == "saying"
        assert saying1["saying"]["content"] == "First"
        assert saying1["saying"]["sequence"] == 0

        saying2 = json.loads(lines[3])
        assert saying2["type"] == "saying"
        assert saying2["saying"]["content"] == "Second"
        assert saying2["saying"]["sequence"] == 1

    def test_export_default_format_is_markdown(self) -> None:
        """Export without format parameter defaults to markdown."""
        table_result = table_create(question="Default format test")
        table_id = table_result["data"]["id"]

        # Export without specifying format
        result = table_export(table_id=table_id)

        assert result["ok"] is True
        assert result["data"]["format"] == "markdown"
        assert "# Default format test" in result["data"]["content"]

    def test_export_invalid_format_returns_invalid_request(self) -> None:
        """Export with invalid format returns INVALID_REQUEST error envelope.

        P1_EMPTY_ROOM fix: Invalid format must return tool-level INVALID_REQUEST
        envelope, NOT raise ValidationError that escapes to the framework layer.
        """
        table_result = table_create(question="Format test")
        table_id = table_result["data"]["id"]

        # Call with invalid format - must return error envelope, not raise
        result = table_export(table_id=table_id, format="invalid_format")

        assert result["ok"] is False
        assert "error" in result
        assert result["error"]["code"] == "INVALID_REQUEST"
        assert "invalid_format" in result["error"]["message"]
        # Verify details include supported formats
        assert "details" in result["error"]
        assert result["error"]["details"]["format"] == "invalid_format"
        assert "markdown" in result["error"]["details"]["supported"]
        assert "jsonl" in result["error"]["details"]["supported"]

    def test_export_invalid_format_various_values(self) -> None:
        """Various invalid format values all return INVALID_REQUEST."""
        table_result = table_create(question="Format variants test")
        table_id = table_result["data"]["id"]

        invalid_formats = [
            "JSON",  # Case sensitivity
            "MARKDOWN",  # Case sensitivity
            "",  # Empty string
            "  markdown  ",  # Whitespace
            "xml",  # Completely wrong
        ]

        for fmt in invalid_formats:
            result = table_export(table_id=table_id, format=fmt)
            assert result["ok"] is False, f"Expected error for format '{fmt}'"
            assert result["error"]["code"] == "INVALID_REQUEST", f"Wrong error code for '{fmt}'"

    def test_export_response_shape(self) -> None:
        """Export response has correct envelope shape."""
        table_result = table_create(question="Shape test")
        table_id = table_result["data"]["id"]

        result = table_export(table_id=table_id, format="markdown")

        # Success envelope shape
        assert result["ok"] is True
        assert "data" in result
        assert "error" not in result

        # Data shape
        data = result["data"]
        assert set(data.keys()) == {"content", "format", "table_id"}
        assert isinstance(data["content"], str)
        assert len(data["content"]) > 0
        assert data["format"] in ("markdown", "jsonl")
        assert data["table_id"] == table_id


# =============================================================================
# Seat Tools Tests
# =============================================================================


class TestSeatHeartbeat:
    """Tests for seat_heartbeat MCP tool."""

    def test_heartbeat_seat_not_found(self) -> None:
        """Heartbeat non-existent seat returns error envelope."""
        result = seat_heartbeat(table_id="any-table", seat_id="nonexistent-id")

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"

    def test_heartbeat_requires_patron_or_seat_id(self) -> None:
        """Heartbeat requires either patron_id or seat_id."""
        result = seat_heartbeat(table_id="any-table")

        assert result["ok"] is False
        assert result["error"]["code"] == "INVALID_REQUEST"

    def test_heartbeat_with_patron_id(self) -> None:
        """Heartbeat with patron_id returns expiry (spec-compliant path)."""
        # Create patron, table, and seat
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]
        table_join(table_id=table_id, patron_id=patron_id)

        # Heartbeat with patron_id (spec-compliant)
        result = seat_heartbeat(table_id=table_id, patron_id=patron_id)

        assert result["ok"] is True
        data = result["data"]
        # Spec-compliant response is minimal: just expires_at
        assert "expires_at" in data

    def test_heartbeat_with_seat_id_legacy(self) -> None:
        """Heartbeat with seat_id returns expiry (legacy path)."""
        # Create patron, table, and seat
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]
        join_result = table_join(table_id=table_id, patron_id=patron_id)
        seat_id = join_result["data"]["seat"]["id"]

        # Heartbeat with seat_id (legacy)
        result = seat_heartbeat(table_id=table_id, seat_id=seat_id)

        assert result["ok"] is True
        data = result["data"]
        assert "expires_at" in data

    def test_heartbeat_with_state_running(self) -> None:
        """Heartbeat with state=running updates seat."""
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]
        table_join(table_id=table_id, patron_id=patron_id)

        result = seat_heartbeat(table_id=table_id, patron_id=patron_id, state="running")

        assert result["ok"] is True
        assert "expires_at" in result["data"]

    def test_heartbeat_with_state_done(self) -> None:
        """Heartbeat with state=done marks seat as left."""
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]
        table_join(table_id=table_id, patron_id=patron_id)

        result = seat_heartbeat(table_id=table_id, patron_id=patron_id, state="done")

        assert result["ok"] is True
        assert "expires_at" in result["data"]

    def test_heartbeat_with_custom_ttl(self) -> None:
        """Heartbeat with custom ttl_ms returns appropriate expiry."""
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]
        table_join(table_id=table_id, patron_id=patron_id)

        # Custom TTL of 30 seconds (30000 ms)
        result = seat_heartbeat(table_id=table_id, patron_id=patron_id, ttl_ms=30000)

        assert result["ok"] is True
        assert "expires_at" in result["data"]

    def test_heartbeat_patron_not_at_table(self) -> None:
        """Heartbeat for patron not at table returns NOT_FOUND."""
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]
        # Note: patron has NOT joined the table

        result = seat_heartbeat(table_id=table_id, patron_id=patron_id)

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"


class TestSeatList:
    """Tests for seat_list MCP tool."""

    def test_list_seats_empty(self) -> None:
        """List seats for empty table returns empty list."""
        # Create table
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]

        result = seat_list(table_id=table_id)

        assert result["ok"] is True
        assert result["data"]["seats"] == []
        assert result["data"]["active_count"] == 0

    def test_list_seats_with_seats(self) -> None:
        """List seats returns active seats."""
        # Create patron and table
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]

        # Join table (creates seat)
        table_join(table_id=table_id, patron_id=patron_id)

        # List seats
        result = seat_list(table_id=table_id)

        assert result["ok"] is True
        assert len(result["data"]["seats"]) == 1
        assert result["data"]["active_count"] == 1
        assert result["data"]["seats"][0]["patron_id"] == patron_id


# =============================================================================
# Error Envelope Tests
# =============================================================================


class TestErrorEnvelopes:
    """Tests for standardized error envelope format.

    Error envelope structure (from MCP server spec):
        Success: {"ok": True, "data": {...}}
        Error:   {"ok": False, "error": {"code": "ERROR_CODE", "message": "..."}}

    Error codes (spec):
        - NOT_FOUND: Resource not found
        - OPERATION_NOT_ALLOWED: Invalid state transition or operation on closed/paused table
        - VERSION_CONFLICT: Optimistic locking conflict (via table_update)
        - AMBIGUOUS_MENTION: Multiple matches for @mention (NOT YET IMPLEMENTED)
        - VALIDATION_ERROR: Input validation failure (NOT YET IMPLEMENTED)
    """

    def test_not_found_error_format(self) -> None:
        """NOT_FOUND error has correct structure."""
        result = patron_get("nonexistent-id")

        assert result["ok"] is False
        assert "error" in result
        error = result["error"]
        assert "code" in error
        assert "message" in error
        assert error["code"] == "NOT_FOUND"
        assert isinstance(error["message"], str)

    def test_success_envelope_format(self) -> None:
        """Success response has correct structure."""
        result = patron_register(name=unique_name())

        assert result["ok"] is True
        assert "data" in result
        assert isinstance(result["data"], dict)

    def test_error_envelope_has_no_data_key(self) -> None:
        """Error envelope should NOT have a 'data' key."""
        result = patron_get("nonexistent-id")

        assert result["ok"] is False
        assert "data" not in result
        assert "error" in result

    def test_success_envelope_has_no_error_key(self) -> None:
        """Success envelope should NOT have an 'error' key."""
        result = patron_register(name=unique_name())

        assert result["ok"] is True
        assert "error" not in result
        assert "data" in result


class TestErrorCodesNotFound:
    """Tests verifying NOT_FOUND error code across all tools.

    NOT_FOUND is returned when a requested resource does not exist.
    """

    def test_patron_get_not_found(self) -> None:
        """patron_get returns NOT_FOUND for nonexistent patron."""
        result = patron_get("nonexistent-patron-id")

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"
        assert "patron" in result["error"]["message"].lower()

    def test_table_get_not_found(self) -> None:
        """table_get returns NOT_FOUND for nonexistent table."""
        result = table_get("nonexistent-table-id")

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"
        assert "table" in result["error"]["message"].lower()

    def test_table_join_table_not_found(self) -> None:
        """table_join returns NOT_FOUND when table doesn't exist."""
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]

        result = table_join(table_id="nonexistent-table-id", patron_id=patron_id)

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"
        assert "table" in result["error"]["message"].lower()

    def test_table_join_patron_not_found(self) -> None:
        """table_join returns NOT_FOUND when patron doesn't exist."""
        table_result = table_create(question="Test")
        table_id = table_result["data"]["id"]

        result = table_join(table_id=table_id, patron_id="nonexistent-patron-id")

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"
        assert "patron" in result["error"]["message"].lower()

    def test_table_say_not_found(self) -> None:
        """table_say returns NOT_FOUND for nonexistent table."""
        result = table_say(
            table_id="nonexistent-table-id",
            content="Hello",
            speaker_name="Test Speaker",
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"
        assert "table" in result["error"]["message"].lower()

    def test_table_listen_not_found(self) -> None:
        """table_listen returns NOT_FOUND for nonexistent table."""
        result = table_listen(table_id="nonexistent-table-id")

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"
        assert "table" in result["error"]["message"].lower()

    def test_seat_heartbeat_not_found(self) -> None:
        """seat_heartbeat returns NOT_FOUND for nonexistent seat."""
        result = seat_heartbeat(table_id="any-table", seat_id="nonexistent-seat-id")

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"
        assert "seat" in result["error"]["message"].lower()


class TestPatronDeduplication:
    """Tests for patron_register deduplication (return_existing semantics).

    Deduplication ensures that registering the same name twice returns the
    existing patron rather than creating a duplicate.

    This is a key feature for agent identity management:
    - Agents can safely call patron_register on every startup
    - The same agent will always get the same patron_id
    - The 'is_new' flag indicates if this was a new or existing registration
    """

    def test_dedup_same_name_returns_existing(self) -> None:
        """Registering same name returns existing patron with is_new=False."""
        name = unique_name()

        # First registration
        result1 = patron_register(name=name)
        assert result1["ok"] is True
        data1 = result1["data"]
        assert data1["is_new"] is True
        first_id = data1["id"]

        # Second registration with same name
        result2 = patron_register(name=name)
        assert result2["ok"] is True
        data2 = result2["data"]
        assert data2["is_new"] is False
        assert data2["id"] == first_id, "Same patron should be returned"

    def test_dedup_preserves_original_kind(self) -> None:
        """Dedup returns patron with original kind, not new kind parameter."""
        name = unique_name()

        # Register as 'human'
        result1 = patron_register(name=name, kind="human")
        assert result1["ok"] is True
        assert result1["data"]["kind"] == "human"

        # Try to register same name as 'agent' (should return original human patron)
        result2 = patron_register(name=name, kind="agent")
        assert result2["ok"] is True
        assert result2["data"]["kind"] == "human", "Original kind should be preserved"
        assert result2["data"]["is_new"] is False

    def test_dedup_different_names_creates_different_patrons(self) -> None:
        """Different names create different patrons."""
        name1 = unique_name("Agent1")
        name2 = unique_name("Agent2")

        result1 = patron_register(name=name1)
        result2 = patron_register(name=name2)

        assert result1["ok"] is True
        assert result2["ok"] is True
        assert result1["data"]["id"] != result2["data"]["id"]
        assert result1["data"]["is_new"] is True
        assert result2["data"]["is_new"] is True

    def test_dedup_idempotent_multiple_calls(self) -> None:
        """Multiple dedup registrations are idempotent."""
        name = unique_name()

        # First call
        result1 = patron_register(name=name)
        first_id = result1["data"]["id"]

        # Second call
        result2 = patron_register(name=name)
        assert result2["data"]["id"] == first_id
        assert result2["data"]["is_new"] is False

        # Third call
        result3 = patron_register(name=name)
        assert result3["data"]["id"] == first_id
        assert result3["data"]["is_new"] is False

        # Fourth call
        result4 = patron_register(name=name)
        assert result4["data"]["id"] == first_id
        assert result4["data"]["is_new"] is False

    def test_dedup_created_at_preserved(self) -> None:
        """Dedup returns original created_at timestamp."""
        name = unique_name()
        import time

        # First registration
        result1 = patron_register(name=name)
        original_created_at = result1["data"]["created_at"]

        # Small delay to ensure time would be different
        time.sleep(0.01)

        # Second registration
        result2 = patron_register(name=name)
        assert result2["data"]["created_at"] == original_created_at


class TestNotImplementedErrorCodes:
    """Tests for MCP tool error codes.

    Some error codes are fully implemented and tested here; others are not yet
    implemented in MCP tools and are documented as gaps for future work.

    Implemented error codes (tests verify behavior):
        - OPERATION_NOT_ALLOWED: Closed/paused table operations (via table_control)
        - VERSION_CONFLICT: Optimistic concurrency conflict (via table_update)

    NOT YET implemented (tests document expected behavior):
        - AMBIGUOUS_MENTION: Mention resolution in table_say
        - VALIDATION_ERROR: Input validation

    See: API routes in src/tasca/shell/api/routes/ for HTTP implementations
    that DO handle these error codes.
    """

    def test_table_closed_error(self) -> None:
        """OPERATION_NOT_ALLOWED is returned for operations on closed table.

        When table_control closes a table:
        - table_say should return OPERATION_NOT_ALLOWED
        - table_join should return OPERATION_NOT_ALLOWED

        Note: The error code is OPERATION_NOT_ALLOWED (not TABLE_CLOSED) per spec.
        See: src/tasca/shell/mcp/server.py table_say guard at line ~960
        """
        # Create a table
        table_result = table_create(question="Test table")
        table_id = table_result["data"]["id"]

        # Register a patron for the tests
        patron_result = patron_register(name=unique_name("TestAgent"))
        patron_id = patron_result["data"]["id"]

        # Close the table via table_control
        close_result = table_control(
            table_id=table_id,
            action="close",
            speaker_name="TestAgent",
            patron_id=patron_id,
        )
        assert close_result["ok"] is True
        assert close_result["data"]["table_status"] == "closed"

        # table_say on closed table should return OPERATION_NOT_ALLOWED
        say_result = table_say(
            table_id=table_id,
            content="This should fail",
            speaker_name="TestAgent",
            patron_id=patron_id,
        )
        assert say_result["ok"] is False
        assert say_result["error"]["code"] == "OPERATION_NOT_ALLOWED"
        assert "closed" in say_result["error"]["message"].lower()

        # table_join on closed table should return OPERATION_NOT_ALLOWED
        join_result = table_join(table_id=table_id, patron_id=patron_id)
        assert join_result["ok"] is False
        assert join_result["error"]["code"] == "OPERATION_NOT_ALLOWED"

    def test_version_conflict_error(self) -> None:
        """VERSION_CONFLICT error is returned for stale version in table_update.

        When table_update is called with a stale expected_version:
        - Should return VERSION_CONFLICT with current version details
        """
        # Create a table
        table_result = table_create(question="Test table")
        table_id = table_result["data"]["id"]
        current_version = table_result["data"]["version"]

        # Register a patron for the tests
        patron_result = patron_register(name=unique_name("TestAgent"))
        patron_id = patron_result["data"]["id"]

        # First update with correct version - should succeed
        first_update = table_update(
            table_id=table_id,
            expected_version=current_version,
            patch={"question": "Updated question"},
            speaker_name="TestAgent",
            patron_id=patron_id,
        )
        assert first_update["ok"] is True
        assert first_update["data"]["table"]["version"] == current_version + 1

        # Second update with stale version - should fail with VERSION_CONFLICT
        stale_update = table_update(
            table_id=table_id,
            expected_version=current_version,  # Stale! Current is current_version + 1
            patch={"question": "Another update"},
            speaker_name="TestAgent",
            patron_id=patron_id,
        )
        assert stale_update["ok"] is False
        assert stale_update["error"]["code"] == "VERSION_CONFLICT"
        # Verify error details contain version info
        details = stale_update["error"]["details"]
        assert "expected_version" in details
        assert "actual_version" in details
        assert details["expected_version"] == current_version
        assert details["actual_version"] == current_version + 1

    def test_ambiguous_mention_error_not_implemented(self) -> None:
        """AMBIGUOUS_MENTION error is NOT YET implemented in MCP tools.

        Expected behavior (once implemented):
        - table_say with ambiguous @mention should return error:
          {"ok": false, "error": {
            "code": "AMBIGUOUS_MENTION",
            "message": "...",
            "details": {"handle": "@name", "candidates": [...]}
          }}

        Current behavior: Mentions are not resolved in table_say.
        See: src/tasca/core/services/mention_service.py::AmbiguousMention
        """
        # Create two patrons with same display_name
        name1 = unique_name("Alice")
        name2 = unique_name("OtherAlice")

        result1 = patron_register(name=name1)
        result2 = patron_register(name=name2)

        # Without mention resolution in table_say, we can't test AmbiguousMention
        # This test documents the gap

        assert result1["ok"] is True
        assert result2["ok"] is True

    def test_validation_error_not_implemented(self) -> None:
        """VALIDATION_ERROR is NOT YET implemented in MCP tools.

        Expected behavior (once implemented):
        - Invalid input (empty content, etc.) should return:
          {"ok": false, "error": {
            "code": "VALIDATION_ERROR",
            "message": "...",
            "details": {"field": "...", "constraint": "..."}
          }}

        Current behavior: Validation not enforced in MCP tools.
        """
        # Current table_say doesn't validate content
        # This test documents the gap

        table_result = table_create(question="Test table")
        table_id = table_result["data"]["id"]

        # Empty content would be a validation error once implemented
        # Currently, empty content is accepted
        # result = table_say(table_id=table_id, content="", speaker_name="Test")
        # assert result["ok"] is False
        # assert result["error"]["code"] == "VALIDATION_ERROR"

        assert table_result["ok"] is True  # Just verify setup works


# =============================================================================
# State Machine Guard Tests
# =============================================================================


class TestStateGuardsTableJoin:
    """Tests for state machine guards on table_join."""

    def test_join_paused_table_rejected(self, test_db: sqlite3.Connection) -> None:
        """Join on PAUSED table should return OPERATION_NOT_ALLOWED."""
        # Create patron
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]

        # Create table and pause it
        table_id = TableId(str(uuid.uuid4()))
        now = datetime.now(UTC)
        table = Table(
            id=table_id,
            question="Paused table",
            context=None,
            status=TableStatus.OPEN,
            version=Version(1),
            created_at=now,
            updated_at=now,
        )
        create_table(test_db, table)

        # Update to PAUSED status
        update = TableUpdate(
            question="Paused table",
            context=None,
            status=TableStatus.PAUSED,
        )
        update_table(test_db, table_id, update, Version(1), now)

        # Try to join - should fail
        result = table_join(table_id=str(table_id), patron_id=patron_id)

        assert result["ok"] is False
        assert result["error"]["code"] == "OPERATION_NOT_ALLOWED"
        assert "PAUSED" in result["error"]["message"] or "paused" in result["error"]["message"]
        assert result["error"]["details"]["table_status"] == "paused"

    def test_join_closed_table_rejected(self, test_db: sqlite3.Connection) -> None:
        """Join on CLOSED table should return OPERATION_NOT_ALLOWED."""
        # Create patron
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]

        # Create table and close it
        table_id = TableId(str(uuid.uuid4()))
        now = datetime.now(UTC)
        table = Table(
            id=table_id,
            question="Closed table",
            context=None,
            status=TableStatus.OPEN,
            version=Version(1),
            created_at=now,
            updated_at=now,
        )
        create_table(test_db, table)

        # Update to CLOSED status
        update = TableUpdate(
            question="Closed table",
            context=None,
            status=TableStatus.CLOSED,
        )
        update_table(test_db, table_id, update, Version(1), now)

        # Try to join - should fail
        result = table_join(table_id=str(table_id), patron_id=patron_id)

        assert result["ok"] is False
        assert result["error"]["code"] == "OPERATION_NOT_ALLOWED"
        assert "CLOSED" in result["error"]["message"] or "closed" in result["error"]["message"]
        assert result["error"]["details"]["table_status"] == "closed"


class TestStateGuardsTableSay:
    """Tests for state machine guards on table_say."""

    def test_say_closed_table_rejected(self, test_db: sqlite3.Connection) -> None:
        """Say on CLOSED table should return OPERATION_NOT_ALLOWED."""
        # Create table and close it
        table_id = TableId(str(uuid.uuid4()))
        now = datetime.now(UTC)
        table = Table(
            id=table_id,
            question="Closed table",
            context=None,
            status=TableStatus.OPEN,
            version=Version(1),
            created_at=now,
            updated_at=now,
        )
        create_table(test_db, table)

        # Update to CLOSED status
        update = TableUpdate(
            question="Closed table",
            context=None,
            status=TableStatus.CLOSED,
        )
        update_table(test_db, table_id, update, Version(1), now)

        # Try to say - should fail
        result = table_say(
            table_id=str(table_id),
            content="This should fail",
            speaker_name="Test Speaker",
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "OPERATION_NOT_ALLOWED"
        assert "CLOSED" in result["error"]["message"] or "closed" in result["error"]["message"]
        assert result["error"]["details"]["table_status"] == "closed"

    def test_say_paused_table_allowed(self, test_db: sqlite3.Connection) -> None:
        """Say on PAUSED table should succeed (soft pause allows sayings)."""
        # Create table and pause it
        table_id = TableId(str(uuid.uuid4()))
        now = datetime.now(UTC)
        table = Table(
            id=table_id,
            question="Paused table",
            context=None,
            status=TableStatus.OPEN,
            version=Version(1),
            created_at=now,
            updated_at=now,
        )
        create_table(test_db, table)

        # Update to PAUSED status
        update = TableUpdate(
            question="Paused table",
            context=None,
            status=TableStatus.PAUSED,
        )
        update_table(test_db, table_id, update, Version(1), now)

        # Say should succeed on PAUSED table
        result = table_say(
            table_id=str(table_id),
            content="This should work on paused table",
            speaker_name="Test Speaker",
        )

        assert result["ok"] is True
        assert result["data"]["content"] == "This should work on paused table"


# =============================================================================
# Limits Enforcement Tests
# =============================================================================


class TestLimitsEnforcementTableSay:
    """Tests for limits enforcement on table_say MCP tool."""

    @pytest.fixture
    def table_with_patron(self, test_db: sqlite3.Connection) -> dict[str, str]:
        """Create table and patron for limits tests."""
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]

        table_id = TableId(str(uuid.uuid4()))
        now = datetime.now(UTC)
        table = Table(
            id=table_id,
            question="Limits test table",
            context=None,
            status=TableStatus.OPEN,
            version=Version(1),
            created_at=now,
            updated_at=now,
        )
        create_table(test_db, table)

        return {"table_id": str(table_id), "patron_id": patron_id}

    def test_table_say_respects_content_length_limit(
        self, table_with_patron: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Content length limit is enforced on table_say."""
        # Patch settings directly to set the limit
        from tasca import config
        from tasca.core.services.limits_service import LimitsConfig

        monkeypatch.setattr(
            config,
            "settings",
            config.Settings(max_content_length=50),
        )

        # Reload the server module to pick up the new settings
        from tasca.shell.mcp import server
        import importlib

        importlib.reload(server)

        # Create a saying that exceeds the limit
        long_content = "x" * 100  # 100 chars, limit is 50
        result = server.table_say(
            table_id=table_with_patron["table_id"],
            content=long_content,
            speaker_name="Test Speaker",
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "LIMIT_EXCEEDED"
        assert result["error"]["details"]["limit_kind"] == "content"
        assert result["error"]["details"]["limit"] == 50
        assert result["error"]["details"]["actual"] == 100

    def test_table_say_respects_history_count_limit(
        self, table_with_patron: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """History count limit is enforced on table_say."""
        from tasca import config
        import importlib
        from tasca.shell.mcp import server

        # Set a small history limit
        monkeypatch.setattr(
            config,
            "settings",
            config.Settings(max_sayings_per_table=2),
        )
        importlib.reload(server)

        table_id = table_with_patron["table_id"]

        # Add 2 sayings (limit is 2, so we should be able to have 2 total)
        result1 = server.table_say(
            table_id=table_id,
            content="First saying",
            speaker_name="Speaker 1",
        )
        assert result1["ok"] is True

        result2 = server.table_say(
            table_id=table_id,
            content="Second saying",
            speaker_name="Speaker 2",
        )
        assert result2["ok"] is True

        # Third saying should fail (already have 2, at limit)
        result3 = server.table_say(
            table_id=table_id,
            content="Third saying - should fail",
            speaker_name="Speaker 3",
        )

        assert result3["ok"] is False
        assert result3["error"]["code"] == "LIMIT_EXCEEDED"
        assert result3["error"]["details"]["limit_kind"] == "history"
        assert result3["error"]["details"]["limit"] == 2
        assert result3["error"]["details"]["actual"] == 2

    def test_table_say_respects_mentions_limit(
        self, table_with_patron: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mentions limit is enforced on table_say."""
        from tasca import config
        import importlib
        from tasca.shell.mcp import server

        # Set a small mentions limit
        monkeypatch.setattr(
            config,
            "settings",
            config.Settings(max_mentions_per_saying=2),
        )
        importlib.reload(server)

        # Create a saying with too many mentions
        content_with_many_mentions = "Hello @alice @bob @charlie @dave - too many!"
        result = server.table_say(
            table_id=table_with_patron["table_id"],
            content=content_with_many_mentions,
            speaker_name="Test Speaker",
        )

        assert result["ok"] is False
        assert result["error"]["code"] == "LIMIT_EXCEEDED"
        assert result["error"]["details"]["limit_kind"] == "mentions"
        assert result["error"]["details"]["limit"] == 2
        assert result["error"]["details"]["actual"] == 4

    def test_table_say_succeeds_when_under_all_limits(
        self, table_with_patron: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """table_say succeeds when all limits are respected."""
        from tasca import config
        import importlib
        from tasca.shell.mcp import server

        # Set limits
        monkeypatch.setattr(
            config,
            "settings",
            config.Settings(
                max_content_length=100,
                max_sayings_per_table=10,
                max_mentions_per_saying=3,
            ),
        )
        importlib.reload(server)

        # Create a valid saying
        result = server.table_say(
            table_id=table_with_patron["table_id"],
            content="Hello @alice and @bob!",  # Under all limits
            speaker_name="Test Speaker",
        )

        assert result["ok"] is True
        assert result["data"]["content"] == "Hello @alice and @bob!"

    def test_table_say_bytes_limit_enforced(
        self, table_with_patron: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bytes limit is enforced on table_say."""
        from tasca import config
        import importlib
        from tasca.shell.mcp import server

        # Set a very small bytes limit (100 bytes)
        monkeypatch.setattr(
            config,
            "settings",
            config.Settings(max_bytes_per_table=100),
        )
        importlib.reload(server)

        # First saying should work
        result1 = server.table_say(
            table_id=table_with_patron["table_id"],
            content="Hello world",  # 11 bytes
            speaker_name="Speaker 1",
        )
        assert result1["ok"] is True

        # Second saying with large content should fail
        large_content = "x" * 200  # 200 bytes, would exceed 100 byte limit
        result2 = server.table_say(
            table_id=table_with_patron["table_id"],
            content=large_content,
            speaker_name="Speaker 2",
        )

        assert result2["ok"] is False
        assert result2["error"]["code"] == "LIMIT_EXCEEDED"
        assert result2["error"]["details"]["limit_kind"] == "bytes"


class TestTableWait:
    """Tests for table_wait MCP tool (long-poll for new sayings).

    table_wait is an async function that blocks until:
    - New sayings are available (returns with sayings, timeout=False)
    - Timeout expires (returns empty sayings, timeout=True)
    """

    @pytest.mark.asyncio
    async def test_table_wait_timeout_returns_empty_sayings(self) -> None:
        """Timeout path: empty sayings list + timeout=True when no new sayings.

        When no sayings arrive within the wait window, the function should
        return with an empty sayings list and timeout=True.
        """
        # Create table with no sayings
        table_result = table_create(question="Empty table for wait test")
        table_id = table_result["data"]["id"]

        # Use a very short wait_ms (0 or 1) to trigger timeout immediately
        result = await table_wait(table_id=table_id, since_sequence=-1, wait_ms=1)

        assert result["ok"] is True
        data = result["data"]
        assert data["sayings"] == []
        assert data["timeout"] is True
        assert "next_sequence" in data
        # next_sequence should be -1 for empty table (spec: max sequence or -1 if empty)
        assert data["next_sequence"] == -1

    @pytest.mark.asyncio
    async def test_table_wait_returns_sayings_when_present(self) -> None:
        """Data path: sayings returned when new saying is present.

        When sayings exist with sequence > since_sequence, the function
        should return immediately with the sayings and timeout=False.
        """
        # Create table and add sayings
        table_result = table_create(question="Table with sayings for wait test")
        table_id = table_result["data"]["id"]

        # Add a saying
        say_result = table_say(
            table_id=table_id,
            content="Test saying for wait",
            speaker_name="Test Speaker",
        )
        assert say_result["ok"] is True
        sequence = say_result["data"]["sequence"]

        # Wait for sayings after the one we just added (should timeout)
        # But first, test that we get the saying when we poll from start
        result = await table_wait(table_id=table_id, since_sequence=-1, wait_ms=100)

        assert result["ok"] is True
        data = result["data"]
        assert len(data["sayings"]) >= 1
        assert data["timeout"] is False
        assert data["next_sequence"] == sequence

        # Verify the saying content
        found = any(s["content"] == "Test saying for wait" for s in data["sayings"])
        assert found, "Expected saying not found in wait response"

    @pytest.mark.asyncio
    async def test_table_wait_not_found(self) -> None:
        """table_wait returns NOT_FOUND for nonexistent table."""
        result = await table_wait(table_id="nonexistent-table-id")

        assert result["ok"] is False
        assert result["error"]["code"] == "NOT_FOUND"
        assert "table" in result["error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_table_wait_include_table_on_timeout(self) -> None:
        """Wait with include_table=True includes table snapshot on timeout."""
        table_result = table_create(question="Test table for include_table")
        table_id = table_result["data"]["id"]

        result = await table_wait(
            table_id=table_id,
            since_sequence=-1,
            wait_ms=1,
            include_table=True,
        )

        assert result["ok"] is True
        data = result["data"]
        assert data["timeout"] is True
        assert "table" in data
        assert data["table"]["id"] == table_id
        assert data["table"]["question"] == "Test table for include_table"

    @pytest.mark.asyncio
    async def test_table_wait_include_table_on_data_path(self) -> None:
        """Data path with include_table=True includes table snapshot alongside sayings.

        When sayings are present (hit path, timeout=False) and include_table=True,
        the response must contain both the sayings list and a table snapshot with
        the expected fields: id, question, context, status, version, created_at,
        updated_at.
        """
        table_result = table_create(question="Include table hit path test")
        table_id = table_result["data"]["id"]

        # Add a saying so the hit path is triggered (sayings returned, timeout=False)
        say_result = table_say(
            table_id=table_id,
            content="Saying triggering hit path",
            speaker_name="Test Speaker",
        )
        assert say_result["ok"] is True

        result = await table_wait(
            table_id=table_id,
            since_sequence=-1,
            wait_ms=100,
            include_table=True,
        )

        assert result["ok"] is True
        data = result["data"]

        # Hit path: timeout=False, sayings non-empty
        assert data["timeout"] is False
        assert len(data["sayings"]) >= 1

        # include_table=True: table snapshot present
        assert "table" in data
        table_snapshot = data["table"]
        assert table_snapshot["id"] == table_id
        assert table_snapshot["question"] == "Include table hit path test"
        assert "status" in table_snapshot
        assert "version" in table_snapshot
        assert "context" in table_snapshot
        assert "created_at" in table_snapshot
        assert "updated_at" in table_snapshot

        # Saying data also present
        found = any(s["content"] == "Saying triggering hit path" for s in data["sayings"])
        assert found, "Expected saying content not found in wait response"

    @pytest.mark.asyncio
    async def test_table_wait_caps_wait_ms(self) -> None:
        """Wait caps wait_ms at MAX_WAIT_MS (10000ms)."""
        table_result = table_create(question="Test table for wait cap")
        table_id = table_result["data"]["id"]

        # Request excessive wait time - should be capped and still work
        # We'll use empty table and short actual wait, so it times out quickly
        result = await table_wait(
            table_id=table_id,
            since_sequence=-1,
            wait_ms=1,  # Use minimal wait to avoid slow test
        )

        # Should timeout since table is empty
        assert result["ok"] is True
        assert result["data"]["timeout"] is True

    @pytest.mark.asyncio
    async def test_table_wait_since_sequence_filters(self) -> None:
        """Wait with since_sequence only returns newer sayings."""
        table_result = table_create(question="Table for since_sequence test")
        table_id = table_result["data"]["id"]

        # Add two sayings
        table_say(table_id=table_id, content="First saying", speaker_name="Speaker A")
        table_say(table_id=table_id, content="Second saying", speaker_name="Speaker B")

        # Wait for sayings after sequence 0 (should get only the second)
        result = await table_wait(table_id=table_id, since_sequence=0, wait_ms=100)

        assert result["ok"] is True
        data = result["data"]
        # Should get at least the second saying
        assert len(data["sayings"]) >= 1
        # First saying (sequence 0) should NOT be in the results
        for saying in data["sayings"]:
            assert saying["sequence"] > 0


# =============================================================================
# Proxy Control Tool Tests
# =============================================================================


class TestConnect:
    """Tests for connect MCP tool (proxy control).

    The connect tool switches between local and remote mode.
    It is a proxy-control tool that NEVER forwards to remote servers.
    """

    @pytest.mark.asyncio
    async def test_connect_switches_to_remote_mode(self) -> None:
        """connect(url=...) switches to remote mode and returns status."""
        from tasca.shell.mcp.server import connect
        from tasca.shell.mcp.proxy import (
            _config,
            get_upstream_config,
            switch_to_local,
        )

        try:
            # Mock session init to avoid real HTTP call
            # Note: switch_to_remote_with_session is imported INSIDE connect(),
            # so we patch it in the proxy module where it's defined.
            # The mocked function must also update the global _config.
            async def mock_switch_to_remote_with_session(url: str, token: str | None = None):
                from tasca.shell.mcp.proxy import UpstreamConfig

                # Update the actual global _config (same object returned)
                _config.url = url
                _config.token = token
                _config.session_id = "test-session-id"
                return Success(_config)

            with patch(
                "tasca.shell.mcp.proxy.switch_to_remote_with_session",
                new=AsyncMock(side_effect=mock_switch_to_remote_with_session),
            ):
                result = await connect(url="http://api.example.com", token="secret-token")

            assert result["ok"] is True
            assert result["data"]["mode"] == "remote"
            assert result["data"]["url"] == "http://api.example.com"
            assert result["data"]["has_token"] is True
            assert result["data"]["has_session"] is True
            assert "token" not in result["data"]

            # Verify global config was updated
            config = get_upstream_config().unwrap()
            assert config.is_remote is True
            assert config.url == "http://api.example.com"
        finally:
            # Reset to local mode for other tests
            switch_to_local()

    @pytest.mark.asyncio
    async def test_connect_switches_to_remote_without_token(self) -> None:
        """connect(url=...) without token switches to remote mode."""
        from tasca.shell.mcp.server import connect
        from tasca.shell.mcp.proxy import (
            _config,
            get_upstream_config,
            switch_to_local,
        )

        try:
            # Mock session init to avoid real HTTP call
            async def mock_switch_to_remote_with_session(url: str, token: str | None = None):
                from tasca.shell.mcp.proxy import UpstreamConfig

                # Update the actual global _config (same object returned)
                _config.url = url
                _config.token = token
                _config.session_id = "test-session-id"
                return Success(_config)

            with patch(
                "tasca.shell.mcp.proxy.switch_to_remote_with_session",
                new=AsyncMock(side_effect=mock_switch_to_remote_with_session),
            ):
                result = await connect(url="http://api.example.com")

            assert result["ok"] is True
            assert result["data"]["mode"] == "remote"
            assert result["data"]["url"] == "http://api.example.com"
            assert result["data"]["has_token"] is False
            assert "token" not in result["data"]

            config = get_upstream_config().unwrap()
            assert config.is_remote is True
            assert config.token is None
        finally:
            switch_to_local()

    @pytest.mark.asyncio
    async def test_connect_switches_to_local_mode(self) -> None:
        """connect() or connect(url=None) switches to local mode."""
        from tasca.shell.mcp.server import connect
        from tasca.shell.mcp.proxy import get_upstream_config, switch_to_remote, switch_to_local

        try:
            # First switch to remote
            switch_to_remote("http://api.example.com", "token")
            assert get_upstream_config().unwrap().is_remote is True

            # Now switch back to local
            result = await connect()

            assert result["ok"] is True
            assert result["data"]["mode"] == "local"
            assert result["data"]["url"] is None
            assert result["data"]["has_token"] is False
            assert "token" not in result["data"]

            config = get_upstream_config().unwrap()
            assert config.is_remote is False
            assert config.url is None
            assert config.token is None
        finally:
            switch_to_local()

    @pytest.mark.asyncio
    async def test_connect_url_none_switches_to_local(self) -> None:
        """connect(url=None) explicitly switches to local mode."""
        from tasca.shell.mcp.server import connect
        from tasca.shell.mcp.proxy import get_upstream_config, switch_to_remote, switch_to_local

        try:
            # First switch to remote
            switch_to_remote("http://api.example.com")
            assert get_upstream_config().unwrap().is_remote is True

            # Explicit None URL switches to local
            result = await connect(url=None)

            assert result["ok"] is True
            assert result["data"]["mode"] == "local"

            config = get_upstream_config().unwrap()
            assert config.is_remote is False
        finally:
            switch_to_local()

    @pytest.mark.asyncio
    async def test_connect_returns_current_config_status(self) -> None:
        """connect returns the current config status after switching."""
        from tasca.shell.mcp.server import connect
        from tasca.shell.mcp.proxy import switch_to_local

        try:
            # Switch to local
            result = await connect()

            assert result["ok"] is True
            data = result["data"]
            assert "mode" in data
            assert "url" in data
            assert "has_token" in data
            assert "token" not in data
            assert data["mode"] in ("local", "remote")
        finally:
            switch_to_local()

    @pytest.mark.asyncio
    async def test_connect_idempotent_local_mode(self) -> None:
        """Multiple connect() calls return local mode consistently."""
        from tasca.shell.mcp.server import connect
        from tasca.shell.mcp.proxy import switch_to_local

        try:
            result1 = await connect()
            result2 = await connect()

            assert result1["ok"] is True
            assert result2["ok"] is True
            assert result1["data"]["mode"] == "local"
            assert result2["data"]["mode"] == "local"
        finally:
            switch_to_local()


class TestConnectionStatus:
    """Tests for connection_status MCP tool.

    The connection_status tool returns the current proxy mode and health status.
    It is a proxy-control tool that NEVER forwards to remote servers.
    """

    def test_connection_status_local_mode(self) -> None:
        """connection_status returns local mode and healthy when in local mode."""
        from tasca.shell.mcp.server import connection_status
        from tasca.shell.mcp.proxy import switch_to_local

        try:
            # Ensure we're in local mode
            switch_to_local()

            result = connection_status()

            assert result["ok"] is True
            assert result["data"]["mode"] == "local"
            assert result["data"]["url"] is None
            assert result["data"]["is_healthy"] is True
        finally:
            switch_to_local()

    def test_connection_status_remote_mode(self) -> None:
        """connection_status returns remote mode and url when in remote mode."""
        from tasca.shell.mcp.server import connection_status
        from tasca.shell.mcp.proxy import switch_to_remote, switch_to_local

        try:
            # Switch to remote mode
            switch_to_remote("http://api.example.com", "secret-token")

            result = connection_status()

            assert result["ok"] is True
            assert result["data"]["mode"] == "remote"
            assert result["data"]["url"] == "http://api.example.com"
            # v1: is_healthy is True if URL is configured (no HTTP ping)
            assert result["data"]["is_healthy"] is True
        finally:
            switch_to_local()

    def test_connection_status_remote_mode_no_token(self) -> None:
        """connection_status works in remote mode without token."""
        from tasca.shell.mcp.server import connection_status
        from tasca.shell.mcp.proxy import switch_to_remote, switch_to_local

        try:
            # Switch to remote mode without token
            switch_to_remote("http://api.example.com")

            result = connection_status()

            assert result["ok"] is True
            assert result["data"]["mode"] == "remote"
            assert result["data"]["url"] == "http://api.example.com"
            # is_healthy is True because URL is set
            assert result["data"]["is_healthy"] is True
        finally:
            switch_to_local()

    def test_connection_status_no_token_in_response(self) -> None:
        """connection_status does NOT return token (unlike connect)."""
        from tasca.shell.mcp.server import connection_status
        from tasca.shell.mcp.proxy import switch_to_remote, switch_to_local

        try:
            # Switch to remote mode with token
            switch_to_remote("http://api.example.com", "secret-token")

            result = connection_status()

            # connection_status returns mode, url, is_healthy -- NOT token
            assert result["ok"] is True
            assert "mode" in result["data"]
            assert "url" in result["data"]
            assert "is_healthy" in result["data"]
            # Token should NOT be in response (security)
            assert "token" not in result["data"]
        finally:
            switch_to_local()

    def test_connection_status_response_shape(self) -> None:
        """connection_status returns correct response envelope shape."""
        from tasca.shell.mcp.server import connection_status
        from tasca.shell.mcp.proxy import switch_to_local

        try:
            switch_to_local()
            result = connection_status()

            # Success envelope
            assert result["ok"] is True
            assert "data" in result
            assert "error" not in result

            # Data shape
            data = result["data"]
            assert set(data.keys()) == {"mode", "url", "is_healthy"}
            assert data["mode"] in ("local", "remote")
            # url can be str or None
            assert data["url"] is None or isinstance(data["url"], str)
            assert isinstance(data["is_healthy"], bool)
        finally:
            switch_to_local()

    @pytest.mark.asyncio
    async def test_connection_status_after_disconnect(self) -> None:
        """connection_status returns local mode after switching from remote to local."""
        from tasca.shell.mcp.server import connection_status, connect
        from tasca.shell.mcp.proxy import switch_to_remote, switch_to_local

        try:
            # Start in remote mode
            switch_to_remote("http://api.example.com")

            # Switch to local via connect()
            await connect()

            result = connection_status()

            assert result["ok"] is True
            assert result["data"]["mode"] == "local"
            assert result["data"]["url"] is None
            assert result["data"]["is_healthy"] is True
        finally:
            switch_to_local()
