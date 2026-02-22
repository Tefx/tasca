"""
Table ID generator - Shell layer wrapper for human-readable ID generation.

This module provides I/O-aware ID generation with collision detection.
It wraps the pure core.generate_human_readable_id function with:
1. Random function injection (random.choice)
2. Database collision checking
3. Retry logic with suffix for uniqueness

Shell layer - handles I/O (database checks) and returns Result[T, E].
"""

from __future__ import annotations

import random
import sqlite3

from returns.result import Failure, Result, Success

from tasca.core.domain.table import TableId
from tasca.core.human_readable_ids import generate_human_readable_id
from tasca.shell.storage.table_repo import TableNotFoundError, get_table


# Maximum retries before giving up on collision
MAX_ID_RETRIES = 10


class TableIdGenerationError(Exception):
    """Failed to generate unique table ID after retries."""

    def __init__(self, attempts: int) -> None:
        self.attempts = attempts
        super().__init__(f"Failed to generate unique table ID after {attempts} attempts")


def _check_id_exists(conn: sqlite3.Connection, table_id: str) -> Result[bool, str]:
    """Check if a table ID already exists in the database.

    Args:
        conn: Database connection.
        table_id: The table ID to check.

    Returns:
        Success(True) if ID exists, Success(False) if available.
        Failure with error message on database error.
    """
    result = get_table(conn, TableId(table_id))
    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, TableNotFoundError):
            # ID does not exist - available
            return Success(False)
        # Database error
        return Failure(str(error))
    # ID exists
    return Success(True)


def generate_table_id(conn: sqlite3.Connection) -> Result[TableId, TableIdGenerationError]:
    """Generate a unique human-readable table ID.

    Generates IDs in format: adjective-noun-verb (e.g., "quick-fox-jumps").
    On collision, retries with numeric suffixes (1, 2, 3, ...).

    Args:
        conn: Database connection for collision checking.

    Returns:
        Success with unique TableId, or Failure if unable to generate.

    Example:
        >>> import sqlite3
        >>> conn = sqlite3.connect(":memory:")
        >>> # Assume tables table created
        >>> result = generate_table_id(conn)
        >>> if isinstance(result, Success):
        ...     table_id = result.unwrap()
        ...     # table_id like "quick-fox-jumps"
    """
    # First attempt: no suffix
    table_id_str = generate_human_readable_id(random.choice)

    check_result = _check_id_exists(conn, table_id_str)
    if isinstance(check_result, Failure):
        return Failure(TableIdGenerationError(1))

    if not check_result.unwrap():
        # ID is available
        return Success(TableId(table_id_str))

    # Collision detected - retry with suffixes
    for suffix in range(1, MAX_ID_RETRIES + 1):
        table_id_str = generate_human_readable_id(random.choice, suffix=suffix)

        check_result = _check_id_exists(conn, table_id_str)
        if isinstance(check_result, Failure):
            return Failure(TableIdGenerationError(suffix + 1))

        if not check_result.unwrap():
            # ID is available
            return Success(TableId(table_id_str))

    # Max retries exceeded
    return Failure(TableIdGenerationError(MAX_ID_RETRIES + 1))
