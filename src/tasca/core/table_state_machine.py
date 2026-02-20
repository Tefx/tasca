"""
Table state machine - pure logic for table state transitions.

This module provides state machine operations for table lifecycle.
All functions are pure (no I/O) with @deal.pre/@deal.post contracts.
"""

import deal

from tasca.core.domain.table import TableStatus


# =============================================================================
# State Transitions
# =============================================================================


@deal.pre(lambda status: status in (TableStatus.OPEN, TableStatus.PAUSED))
@deal.post(lambda result: result == TableStatus.CLOSED)
def transition_to_closed(status: TableStatus) -> TableStatus:
    """Transition any non-closed state to CLOSED.

    CLOSED is a terminal state - no further transitions allowed.

    Args:
        status: Current table status (must be OPEN or PAUSED).

    Returns:
        CLOSED status.

    Raises:
        PreconditionError: If status is already CLOSED.

    Doctests:
        >>> transition_to_closed(TableStatus.OPEN)
        <TableStatus.CLOSED: 'closed'>
        >>> transition_to_closed(TableStatus.PAUSED)
        <TableStatus.CLOSED: 'closed'>
    """
    return TableStatus.CLOSED


@deal.pre(lambda status: status == TableStatus.OPEN)
@deal.post(lambda result: result == TableStatus.PAUSED)
def transition_to_paused(status: TableStatus) -> TableStatus:
    """Pause an open table.

    PAUSED is a soft pause: sayings can still be added but new joins
    are blocked. The table can be resumed.

    Args:
        status: Current table status (must be OPEN).

    Returns:
        PAUSED status.

    Raises:
        PreconditionError: If status is not OPEN.

    Doctests:
        >>> transition_to_paused(TableStatus.OPEN)
        <TableStatus.PAUSED: 'paused'>
    """
    return TableStatus.PAUSED


@deal.pre(lambda status: status == TableStatus.PAUSED)
@deal.post(lambda result: result == TableStatus.OPEN)
def transition_to_open(status: TableStatus) -> TableStatus:
    """Resume a paused table.

    Args:
        status: Current table status (must be PAUSED).

    Returns:
        OPEN status.

    Raises:
        PreconditionError: If status is not PAUSED.

    Doctests:
        >>> transition_to_open(TableStatus.PAUSED)
        <TableStatus.OPEN: 'open'>
    """
    return TableStatus.OPEN


# =============================================================================
# Operation Guards
# =============================================================================


@deal.post(lambda result: isinstance(result, bool))
def can_say(status: TableStatus) -> bool:
    """Check if sayings can be added to the table.

    PAUSED allows sayings (soft pause for conversation continuity).
    CLOSED blocks sayings (terminal state).

    Args:
        status: Current table status.

    Returns:
        True if sayings can be added, False otherwise.

    Doctests:
        >>> can_say(TableStatus.OPEN)
        True
        >>> can_say(TableStatus.PAUSED)
        True
        >>> can_say(TableStatus.CLOSED)
        False
    """
    return status != TableStatus.CLOSED


@deal.post(lambda result: isinstance(result, bool))
def can_join(status: TableStatus) -> bool:
    """Check if agents can join the table.

    Only OPEN tables accept new joins.
    PAUSED tables reject joins (soft pause, not frozen).
    CLOSED tables reject joins (terminal state).

    Args:
        status: Current table status.

    Returns:
        True if joins are allowed, False otherwise.

    Doctests:
        >>> can_join(TableStatus.OPEN)
        True
        >>> can_join(TableStatus.PAUSED)
        False
        >>> can_join(TableStatus.CLOSED)
        False
    """
    return status == TableStatus.OPEN


@deal.post(lambda result: isinstance(result, bool))
def is_terminal(status: TableStatus) -> bool:
    """Check if the state is terminal (no transitions allowed).

    CLOSED is the only terminal state.

    Args:
        status: Current table status.

    Returns:
        True if no transitions are allowed, False otherwise.

    Doctests:
        >>> is_terminal(TableStatus.OPEN)
        False
        >>> is_terminal(TableStatus.PAUSED)
        False
        >>> is_terminal(TableStatus.CLOSED)
        True
    """
    return status == TableStatus.CLOSED


# =============================================================================
# State Queries
# =============================================================================


@deal.post(lambda result: isinstance(result, bool))
def is_open(status: TableStatus) -> bool:
    """Check if the table is open (active).

    Args:
        status: Current table status.

    Returns:
        True if the table is open, False otherwise.

    Doctests:
        >>> is_open(TableStatus.OPEN)
        True
        >>> is_open(TableStatus.PAUSED)
        False
        >>> is_open(TableStatus.CLOSED)
        False
    """
    return status == TableStatus.OPEN


@deal.post(lambda result: isinstance(result, bool))
def is_paused(status: TableStatus) -> bool:
    """Check if the table is paused.

    Args:
        status: Current table status.

    Returns:
        True if the table is paused, False otherwise.

    Doctests:
        >>> is_paused(TableStatus.OPEN)
        False
        >>> is_paused(TableStatus.PAUSED)
        True
        >>> is_paused(TableStatus.CLOSED)
        False
    """
    return status == TableStatus.PAUSED


@deal.post(lambda result: isinstance(result, bool))
def is_closed(status: TableStatus) -> bool:
    """Check if the table is closed.

    Args:
        status: Current table status.

    Returns:
        True if the table is closed, False otherwise.

    Doctests:
        >>> is_closed(TableStatus.OPEN)
        False
        >>> is_closed(TableStatus.PAUSED)
        False
        >>> is_closed(TableStatus.CLOSED)
        True
    """
    return status == TableStatus.CLOSED


# =============================================================================
# Valid Transitions
# =============================================================================


@deal.post(lambda result: isinstance(result, bool))
def can_transition_to_paused(status: TableStatus) -> bool:
    """Check if the table can transition to paused state.

    Only OPEN tables can be paused.

    Args:
        status: Current table status.

    Returns:
        True if transition to paused is valid, False otherwise.

    Doctests:
        >>> can_transition_to_paused(TableStatus.OPEN)
        True
        >>> can_transition_to_paused(TableStatus.PAUSED)
        False
        >>> can_transition_to_paused(TableStatus.CLOSED)
        False
    """
    return status == TableStatus.OPEN


@deal.post(lambda result: isinstance(result, bool))
def can_transition_to_open(status: TableStatus) -> bool:
    """Check if the table can transition to open state.

    Only PAUSED tables can be resumed.

    Args:
        status: Current table status.

    Returns:
        True if transition to open is valid, False otherwise.

    Doctests:
        >>> can_transition_to_open(TableStatus.OPEN)
        False
        >>> can_transition_to_open(TableStatus.PAUSED)
        True
        >>> can_transition_to_open(TableStatus.CLOSED)
        False
    """
    return status == TableStatus.PAUSED


@deal.post(lambda result: isinstance(result, bool))
def can_transition_to_closed(status: TableStatus) -> bool:
    """Check if the table can be closed.

    OPEN and PAUSED tables can be closed.
    CLOSED tables cannot be closed again.

    Args:
        status: Current table status.

    Returns:
        True if transition to closed is valid, False otherwise.

    Doctests:
        >>> can_transition_to_closed(TableStatus.OPEN)
        True
        >>> can_transition_to_closed(TableStatus.PAUSED)
        True
        >>> can_transition_to_closed(TableStatus.CLOSED)
        False
    """
    return status in (TableStatus.OPEN, TableStatus.PAUSED)
