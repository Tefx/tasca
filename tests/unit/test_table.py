"""
Unit tests for Table domain types and state machine.
"""

import pytest

from tasca.core.domain.table import Table, TableCreate, TableId, TableStatus
from tasca.core.table_state_machine import (
    can_join,
    can_say,
    can_transition_to_closed,
    can_transition_to_open,
    can_transition_to_paused,
    is_closed,
    is_open,
    is_paused,
    is_terminal,
    transition_to_closed,
    transition_to_open,
    transition_to_paused,
)


class TestTableStatus:
    """Tests for TableStatus enum."""

    def test_status_values(self) -> None:
        """TableStatus has correct string values."""
        assert TableStatus.OPEN.value == "open"
        assert TableStatus.PAUSED.value == "paused"
        assert TableStatus.CLOSED.value == "closed"

    def test_status_count(self) -> None:
        """TableStatus has exactly three states."""
        assert len(TableStatus) == 3


class TestTableStatusInheritance:
    """Tests for TableStatus as str enum."""

    def test_status_is_string(self) -> None:
        """TableStatus values are strings."""
        assert isinstance(TableStatus.OPEN, str)
        assert TableStatus.OPEN == "open"


class TestTransitionToPaused:
    """Tests for transition_to_paused."""

    def test_open_to_paused(self) -> None:
        """OPEN tables can be paused."""
        assert transition_to_paused(TableStatus.OPEN) == TableStatus.PAUSED

    def test_paused_to_paused_raises(self) -> None:
        """PAUSED tables cannot be paused again."""
        with pytest.raises(Exception):  # PreconditionError
            transition_to_paused(TableStatus.PAUSED)

    def test_closed_to_paused_raises(self) -> None:
        """CLOSED tables cannot be paused."""
        with pytest.raises(Exception):  # PreconditionError
            transition_to_paused(TableStatus.CLOSED)


class TestTransitionToOpen:
    """Tests for transition_to_open."""

    def test_paused_to_open(self) -> None:
        """PAUSED tables can be resumed."""
        assert transition_to_open(TableStatus.PAUSED) == TableStatus.OPEN

    def test_open_to_open_raises(self) -> None:
        """OPEN tables cannot be resumed."""
        with pytest.raises(Exception):  # PreconditionError
            transition_to_open(TableStatus.OPEN)

    def test_closed_to_open_raises(self) -> None:
        """CLOSED tables cannot be resumed."""
        with pytest.raises(Exception):  # PreconditionError
            transition_to_open(TableStatus.CLOSED)


class TestTransitionToClosed:
    """Tests for transition_to_closed."""

    def test_open_to_closed(self) -> None:
        """OPEN tables can be closed."""
        assert transition_to_closed(TableStatus.OPEN) == TableStatus.CLOSED

    def test_paused_to_closed(self) -> None:
        """PAUSED tables can be closed."""
        assert transition_to_closed(TableStatus.PAUSED) == TableStatus.CLOSED

    def test_closed_to_closed_raises(self) -> None:
        """CLOSED tables cannot be closed again."""
        with pytest.raises(Exception):  # PreconditionError
            transition_to_closed(TableStatus.CLOSED)


class TestCanSay:
    """Tests for can_say (soft pause for sayings)."""

    def test_open_can_say(self) -> None:
        """OPEN tables accept sayings."""
        assert can_say(TableStatus.OPEN) is True

    def test_paused_can_say(self) -> None:
        """PAUSED tables accept sayings (soft pause)."""
        assert can_say(TableStatus.PAUSED) is True

    def test_closed_cannot_say(self) -> None:
        """CLOSED tables reject sayings (terminal)."""
        assert can_say(TableStatus.CLOSED) is False


class TestCanJoin:
    """Tests for can_join."""

    def test_open_can_join(self) -> None:
        """OPEN tables accept joins."""
        assert can_join(TableStatus.OPEN) is True

    def test_paused_cannot_join(self) -> None:
        """PAUSED tables reject joins."""
        assert can_join(TableStatus.PAUSED) is False

    def test_closed_cannot_join(self) -> None:
        """CLOSED tables reject joins."""
        assert can_join(TableStatus.CLOSED) is False


class TestIsTerminal:
    """Tests for is_terminal."""

    def test_open_not_terminal(self) -> None:
        """OPEN is not terminal."""
        assert is_terminal(TableStatus.OPEN) is False

    def test_paused_not_terminal(self) -> None:
        """PAUSED is not terminal."""
        assert is_terminal(TableStatus.PAUSED) is False

    def test_closed_is_terminal(self) -> None:
        """CLOSED is terminal."""
        assert is_terminal(TableStatus.CLOSED) is True


class TestStateQueries:
    """Tests for state query functions."""

    def test_is_open(self) -> None:
        """is_open returns correct values."""
        assert is_open(TableStatus.OPEN) is True
        assert is_open(TableStatus.PAUSED) is False
        assert is_open(TableStatus.CLOSED) is False

    def test_is_paused(self) -> None:
        """is_paused returns correct values."""
        assert is_paused(TableStatus.OPEN) is False
        assert is_paused(TableStatus.PAUSED) is True
        assert is_paused(TableStatus.CLOSED) is False

    def test_is_closed(self) -> None:
        """is_closed returns correct values."""
        assert is_closed(TableStatus.OPEN) is False
        assert is_closed(TableStatus.PAUSED) is False
        assert is_closed(TableStatus.CLOSED) is True


class TestCanTransition:
    """Tests for transition validity checks."""

    def test_can_transition_to_paused(self) -> None:
        """can_transition_to_paused validation."""
        assert can_transition_to_paused(TableStatus.OPEN) is True
        assert can_transition_to_paused(TableStatus.PAUSED) is False
        assert can_transition_to_paused(TableStatus.CLOSED) is False

    def test_can_transition_to_open(self) -> None:
        """can_transition_to_open validation."""
        assert can_transition_to_open(TableStatus.OPEN) is False
        assert can_transition_to_open(TableStatus.PAUSED) is True
        assert can_transition_to_open(TableStatus.CLOSED) is False

    def test_can_transition_to_closed(self) -> None:
        """can_transition_to_closed validation."""
        assert can_transition_to_closed(TableStatus.OPEN) is True
        assert can_transition_to_closed(TableStatus.PAUSED) is True
        assert can_transition_to_closed(TableStatus.CLOSED) is False


class TestTableCreate:
    """Tests for TableCreate model."""

    def test_create_with_question_only(self) -> None:
        """TableCreate works with just a question."""
        data = TableCreate(question="What is the meaning of life?")
        assert data.question == "What is the meaning of life?"
        assert data.context is None

    def test_create_with_context(self) -> None:
        """TableCreate works with question and context."""
        data = TableCreate(
            question="Discuss AI",
            context="Focus on ethical implications",
        )
        assert data.question == "Discuss AI"
        assert data.context == "Focus on ethical implications"


class TestTable:
    """Tests for Table model."""

    def test_default_status_is_open(self) -> None:
        """Table defaults to OPEN status."""
        from datetime import datetime

        table = Table(
            id=TableId("test-id"),
            question="Test question",
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        assert table.status == TableStatus.OPEN
