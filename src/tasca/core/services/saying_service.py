"""
Saying service - core business logic for saying operations.

This module provides pure functions for sequence allocation and saying validation.
All I/O operations are handled by the shell layer (repositories).
"""

import deal


@deal.pre(lambda current_max: current_max >= -1)  # -1 = no existing sayings
@deal.post(lambda result: result >= 0)
def compute_next_sequence(current_max: int) -> int:
    """Compute the next sequence number for a table.

    Sequence numbers are:
    - Monotonically increasing (no gaps allowed in normal operation)
    - Zero-indexed (first saying in table has sequence=0)
    - Per-table (each table has its own sequence)

    Args:
        current_max: Current maximum sequence for the table (-1 if no sayings).

    Returns:
        Next sequence number (current_max + 1).

    Examples:
        >>> compute_next_sequence(-1)  # No existing sayings
        0
        >>> compute_next_sequence(0)   # One saying exists
        1
        >>> compute_next_sequence(5)
        6
        >>> compute_next_sequence(100)
        101
    """
    return current_max + 1


@deal.pre(lambda new_seq, current_max: new_seq >= 0 and current_max >= -1)
@deal.post(lambda result: isinstance(result, bool))
def validate_sequence_is_next(new_seq: int, current_max: int) -> bool:
    """Validate that a sequence number is the correct next value.

    This validation ensures:
    - No sequence gaps (new_seq == current_max + 1)
    - No duplicate sequences
    - Monotonic ordering within a table

    Args:
        new_seq: Proposed sequence number.
        current_max: Current maximum sequence for the table (-1 if no sayings).

    Returns:
        True if new_seq is the correct next sequence.

    Examples:
        >>> validate_sequence_is_next(0, -1)  # First saying
        True
        >>> validate_sequence_is_next(1, 0)
        True
        >>> validate_sequence_is_next(5, 4)
        True
        >>> validate_sequence_is_next(2, 0)  # Gap: skip sequence 1
        False
        >>> validate_sequence_is_next(0, 0)  # Duplicate
        False
    """
    return new_seq == current_max + 1


@deal.post(lambda result: result >= -1)
def get_max_sequence(sequences: list[int]) -> int:
    """Get the maximum sequence from a list.

    This is a pure helper for extracting max sequence from query results.

    Args:
        sequences: List of sequence numbers (may be empty).

    Returns:
        Maximum sequence, or -1 if list is empty (so next would be 0).

    Examples:
        >>> get_max_sequence([1, 2, 5, 3])
        5
        >>> get_max_sequence([0])
        0
        >>> get_max_sequence([])
        -1
    """
    return max(sequences) if sequences else -1


@deal.pre(lambda start_seq, count: start_seq >= 0 and count >= 0)
@deal.post(lambda result: isinstance(result, list))
@deal.ensure(lambda start_seq, count, result: len(result) == count)
def generate_sequence_range(start_seq: int, count: int) -> list[int]:
    """Generate a range of sequence numbers.

    Useful for batch allocation planning (though allocation must still be atomic).

    Args:
        start_seq: Starting sequence number (inclusive).
        count: Number of sequences to generate.

    Returns:
        List of sequence numbers from start_seq to start_seq + count - 1.

    Examples:
        >>> generate_sequence_range(0, 3)
        [0, 1, 2]
        >>> generate_sequence_range(5, 0)
        []
        >>> generate_sequence_range(10, 1)
        [10]
    """
    return list(range(start_seq, start_seq + count))


@deal.pre(lambda seq: seq >= 0)
@deal.post(lambda result: result >= 1)
def sequence_to_order(seq: int) -> int:
    """Convert sequence to display order (1-indexed).

    For UI display, sequences are typically shown starting from 1.

    Args:
        seq: Zero-indexed sequence number.

    Returns:
        One-indexed display order.

    Examples:
        >>> sequence_to_order(0)
        1
        >>> sequence_to_order(5)
        6
    """
    return seq + 1


@deal.pre(lambda order: order >= 1)
@deal.post(lambda result: result >= 0)
def order_to_sequence(order: int) -> int:
    """Convert display order (1-indexed) back to sequence.

    Args:
        order: One-indexed display order.

    Returns:
        Zero-indexed sequence number.

    Examples:
        >>> order_to_sequence(1)
        0
        >>> order_to_sequence(6)
        5
    """
    return order - 1
