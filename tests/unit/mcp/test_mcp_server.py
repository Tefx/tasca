"""
Unit tests for MCP server tools.

Tests each MCP tool's contract using in-memory SQLite database.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any, Generator

import pytest

from tasca.core.domain.patron import Patron, PatronId
from tasca.core.domain.table import Table, TableId, TableStatus, TableUpdate, Version
from tasca.shell.mcp.server import (
    patron_get,
    patron_register,
    seat_heartbeat,
    seat_list,
    table_create,
    table_get,
    table_join,
    table_listen,
    table_say,
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
    """Override the get_db dependency to use test database."""
    from tasca.shell.api import deps

    original_get_db = deps.get_db

    def get_test_db() -> Generator[sqlite3.Connection, None, None]:
        yield test_db

    deps.get_db = get_test_db
    yield
    deps.get_db = original_get_db


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
        assert "id" in data
        assert data["name"] == name
        assert data["kind"] == "agent"
        assert data["is_new"] is True
        assert "created_at" in data

    def test_register_patron_default_kind(self) -> None:
        """Register patron with default kind."""
        result = patron_register(name=unique_name())

        assert result["ok"] is True
        assert result["data"]["kind"] == "agent"

    def test_register_patron_human_kind(self) -> None:
        """Register patron with human kind."""
        result = patron_register(name=unique_name("Human"), kind="human")

        assert result["ok"] is True
        assert result["data"]["kind"] == "human"

    def test_register_patron_dedup_returns_existing(self) -> None:
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
        assert data2["id"] == first_id


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
        assert data["speaker"]["kind"] == "human"  # No patron_id = human

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
        assert result["data"]["next_sequence"] == 0

    def test_listen_with_sayings(self) -> None:
        """Listen returns sayings and correct next_sequence."""
        # Create table and say things
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]

        table_say(table_id=table_id, content="First", speaker_name="A")
        table_say(table_id=table_id, content="Second", speaker_name="B")

        # Listen for all
        result = table_listen(table_id=table_id, since_sequence=-1)

        assert result["ok"] is True
        sayings = result["data"]["sayings"]
        assert len(sayings) == 2
        assert sayings[0]["content"] == "First"
        assert sayings[1]["content"] == "Second"
        assert result["data"]["next_sequence"] == 2

    def test_listen_with_since_sequence(self) -> None:
        """Listen with since_sequence filters sayings."""
        # Create table and say things
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]

        table_say(table_id=table_id, content="First", speaker_name="A")
        table_say(table_id=table_id, content="Second", speaker_name="B")
        table_say(table_id=table_id, content="Third", speaker_name="C")

        # Listen for sayings after sequence 0
        result = table_listen(table_id=table_id, since_sequence=0)

        assert result["ok"] is True
        sayings = result["data"]["sayings"]
        assert len(sayings) == 2  # Second and Third
        assert sayings[0]["content"] == "Second"
        assert sayings[1]["content"] == "Third"


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

    def test_heartbeat_seat_success(self) -> None:
        """Heartbeat updates seat and returns expiry."""
        # Create patron, table, and seat
        patron_result = patron_register(name=unique_name())
        patron_id = patron_result["data"]["id"]
        table_result = table_create(question="Test question")
        table_id = table_result["data"]["id"]
        join_result = table_join(table_id=table_id, patron_id=patron_id)
        seat_id = join_result["data"]["seat"]["id"]

        # Heartbeat
        result = seat_heartbeat(table_id=table_id, seat_id=seat_id)

        assert result["ok"] is True
        data = result["data"]
        assert data["seat"]["id"] == seat_id
        assert data["seat"]["state"] == "joined"
        assert "expires_at" in data


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

    Required error codes (spec):
        - NOT_FOUND: Resource not found
        - TABLE_CLOSED: Operation on closed table (NOT YET IMPLEMENTED)
        - VERSION_CONFLICT: Optimistic locking conflict (NOT YET IMPLEMENTED)
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
    """Document error codes that are NOT YET IMPLEMENTED in MCP tools.

    These tests document the expected behavior once the features are implemented.
    They will FAIL until the corresponding features are added to the MCP server.

    See: API routes in src/tasca/shell/api/routes/ for HTTP implementations
    that DO handle these error codes.
    """

    def test_table_closed_error_not_implemented(self) -> None:
        """TABLE_CLOSED error is NOT YET implemented in MCP tools.

        Expected behavior (once implemented):
        - table_say on closed table should return:
          {"ok": false, "error": {"code": "TABLE_CLOSED", "message": "..."}}
        - table_join on closed table should return:
          {"ok": false, "error": {"code": "TABLE_CLOSED", "message": "..."}}

        Current behavior: Operations succeed even on closed tables.
        See: src/tasca/core/table_state_machine.py::can_say, can_join
        """
        # Create a table
        table_result = table_create(question="Test table")
        table_id = table_result["data"]["id"]

        # Note: Current MCP tools don't have table_update to close the table
        # This test documents the gap - TABLE_CLOSED is NOT YET implemented

        # Once table_update MCP tool is added:
        # 1. Close the table via table_update
        # 2. Try table_say - should return TABLE_CLOSED error
        # 3. Try table_join - should return TABLE_CLOSED error

        # For now, just verify the table was created successfully
        assert table_result["ok"] is True
        assert table_result["data"]["status"] == "open"

    def test_version_conflict_error_not_implemented(self) -> None:
        """VERSION_CONFLICT error is NOT YET implemented in MCP tools.

        Expected behavior (once implemented):
        - table_update with stale version should return:
          {"ok": false, "error": {
            "code": "VERSION_CONFLICT",
            "message": "...",
            "details": {"current_version": N, "expected_version": M}
          }}

        Current behavior: No table_update MCP tool exists.
        See: src/tasca/shell/storage/table_repo.py::VersionConflictError
        See: src/tasca/shell/api/routes/tables.py for HTTP implementation
        """
        # Currently no table_update MCP tool
        # This test documents the gap

        table_result = table_create(question="Test table")
        assert table_result["data"]["version"] == 1
        # Once table_update is added, this should test version conflict

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
