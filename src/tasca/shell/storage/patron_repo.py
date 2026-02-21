"""
Patron repository - SQLite implementation for patron persistence.

This module provides I/O operations for patrons including registration
with deduplication support.
Shell layer - handles I/O (database operations) and returns Result[T, E].
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from returns.result import Failure, Result, Success

from tasca.core.domain.patron import Patron, PatronCreate, PatronId


# =============================================================================
# Error Types
# =============================================================================


class PatronError(Exception):
    """Base error for patron operations."""

    pass


class PatronNotFoundError(PatronError):
    """Patron not found in database."""

    def __init__(self, patron_id: PatronId) -> None:
        self.patron_id = patron_id
        super().__init__(f"Patron not found: {patron_id}")


class PatronDatabaseError(PatronError):
    """Database error during patron operation."""

    pass


# =============================================================================
# Repository Operations
# =============================================================================


# @invar:allow shell_result: Private helper - pure data transformation, not a shell operation
# @shell_orchestration: Helper for row-to-domain mapping, used internally by repo functions
def _row_to_patron(row: tuple) -> Patron:
    """Convert a database row to a Patron object.

    Args:
        row: Database row tuple (id, name, kind, created_at).

    Returns:
        Patron domain object.
    """
    return Patron(
        id=PatronId(row[0]),
        name=row[1],
        kind=row[2],
        created_at=datetime.fromisoformat(row[3]),
    )


# @shell_orchestration: Repository operation with Result type
def create_patron(conn: sqlite3.Connection, patron: Patron) -> Result[Patron, PatronError]:
    """Create a new patron in the database.

    Args:
        conn: Database connection.
        patron: Patron to create.

    Returns:
        Success with the created Patron, or Failure with PatronError.

    Example:
        >>> from tasca.shell.storage.database import apply_schema
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> patron = Patron(
        ...     id=PatronId("test-id"),
        ...     name="Test Agent",
        ...     kind="agent",
        ...     created_at=datetime.now(UTC)
        ... )
        >>> result = create_patron(conn, patron)
        >>> isinstance(result, Success)
        True
        >>> result.unwrap().name == "Test Agent"
        True
        >>> conn.close()
    """
    try:
        conn.execute(
            """
            INSERT INTO patrons (id, name, kind, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                patron.id,
                patron.name,
                patron.kind,
                patron.created_at.isoformat(),
            ),
        )
        conn.commit()
        return Success(patron)
    except sqlite3.IntegrityError as e:
        return Failure(PatronError(f"Patron already exists or constraint violation: {e}"))
    except sqlite3.Error as e:
        return Failure(PatronDatabaseError(f"Failed to create patron: {e}"))


# @shell_orchestration: Repository operation with Result type
def get_patron(conn: sqlite3.Connection, patron_id: PatronId) -> Result[Patron, PatronError]:
    """Get a patron by ID from the database.

    Args:
        conn: Database connection.
        patron_id: ID of the patron to retrieve.

    Returns:
        Success with Patron, or Failure with PatronNotFoundError or PatronDatabaseError.

    Example:
        >>> from tasca.shell.storage.database import apply_schema
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> result = get_patron(conn, PatronId("nonexistent"))
        >>> isinstance(result, Failure)
        True
        >>> isinstance(result.failure(), PatronNotFoundError)
        True
        >>> conn.close()
    """
    try:
        cursor = conn.execute(
            """
            SELECT id, name, kind, created_at
            FROM patrons WHERE id = ?
            """,
            (patron_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return Failure(PatronNotFoundError(patron_id))

        return Success(_row_to_patron(row))
    except sqlite3.Error as e:
        return Failure(PatronDatabaseError(f"Failed to get patron: {e}"))


# @shell_orchestration: Repository operation with Result type
def find_patron_by_name(conn: sqlite3.Connection, name: str) -> Result[Patron | None, PatronError]:
    """Find a patron by name from the database.

    Args:
        conn: Database connection.
        name: Name of the patron to find.

    Returns:
        Success with Patron if found, Success(None) if not found,
        or Failure with PatronDatabaseError.

    Example:
        >>> from tasca.shell.storage.database import apply_schema
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> result = find_patron_by_name(conn, "Nonexistent Agent")
        >>> isinstance(result, Success) and result.unwrap() is None
        True
        >>> conn.close()
    """
    try:
        cursor = conn.execute(
            """
            SELECT id, name, kind, created_at
            FROM patrons WHERE name = ?
            """,
            (name,),
        )
        row = cursor.fetchone()
        if row is None:
            return Success(None)

        return Success(_row_to_patron(row))
    except sqlite3.Error as e:
        return Failure(PatronDatabaseError(f"Failed to find patron: {e}"))
