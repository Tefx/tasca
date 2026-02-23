"""
Table repository - SQLite implementation for table persistence.

This module provides I/O operations for tables including optimistic
concurrency control via version checking.
Shell layer - handles I/O (database operations) and returns Result[T, E].
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from returns.result import Failure, Result, Success

from tasca.core.domain.table import Table, TableId, TableStatus, TableUpdate, Version
from tasca.core.services.table_service import (
    VersionMismatchError,
    prepare_versioned_update,
)


# =============================================================================
# Error Types
# =============================================================================


class TableError(Exception):
    """Base error for table operations."""

    pass


class TableNotFoundError(TableError):
    """Table not found in database."""

    def __init__(self, table_id: TableId) -> None:
        self.table_id = table_id
        super().__init__(f"Table not found: {table_id}")


class VersionConflictError(TableError):
    """Version conflict during optimistic concurrency update.

    This error is raised when the expected_version provided by the client
    does not match the current version in the database. This indicates
    a concurrent modification conflict.

    Attributes:
        table_id: The ID of the table that had the conflict.
        current_version: The actual version in the database.
        expected_version: The version the client expected.
    """

    def __init__(
        self,
        table_id: TableId,
        current_version: Version,
        expected_version: Version,
    ) -> None:
        self.table_id = table_id
        self.current_version = current_version
        self.expected_version = expected_version
        super().__init__(
            f"Version conflict for table {table_id}: "
            f"expected version {expected_version}, but current is {current_version}"
        )

    def to_json(self) -> dict:
        """Convert error to JSON for API responses.

        Returns:
            JSON representation of the conflict error.

        Example:
            >>> error = VersionConflictError(TableId("t1"), Version(3), Version(2))
            >>> error.to_json()
            {'error': 'version_conflict', 'table_id': 't1', 'current_version': 3, 'expected_version': 2, 'message': 'Version conflict for table t1: expected version 2, but current is 3'}
        """
        return {
            "error": "version_conflict",
            "table_id": self.table_id,
            "current_version": self.current_version,
            "expected_version": self.expected_version,
            "message": str(self),
        }


class TableDatabaseError(TableError):
    """Database error during table operation."""

    pass


# =============================================================================
# Repository Operations
# =============================================================================


# @invar:allow shell_result: Private helper - pure data transformation, not a shell operation
# @shell_orchestration: Helper for row-to-domain mapping, used internally by repo functions
def _row_to_table(row: tuple) -> Table:
    """Convert a database row to a Table object."""
    return Table(
        id=TableId(row[0]),
        question=row[1],
        context=row[2],
        status=TableStatus(row[3]),
        version=Version(row[4]),
        created_at=datetime.fromisoformat(row[5]),
        updated_at=datetime.fromisoformat(row[6]),
        creator_patron_id=row[7] if len(row) > 7 else None,
    )


def create_table(conn: sqlite3.Connection, table: Table) -> Result[Table, TableError]:
    """Create a new table in the database.

    Args:
        conn: Database connection.
        table: Table to create.

    Returns:
        Success with the created Table, or Failure with TableError.
    """
    try:
        conn.execute(
            """
            INSERT INTO tables (id, question, context, status, version, created_at, updated_at, creator_patron_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                table.id,
                table.question,
                table.context,
                table.status.value,
                table.version,
                table.created_at.isoformat(),
                table.updated_at.isoformat(),
                table.creator_patron_id,
            ),
        )
        conn.commit()
        return Success(table)
    except sqlite3.IntegrityError as e:
        return Failure(TableError(f"Table already exists or constraint violation: {e}"))
    except sqlite3.Error as e:
        return Failure(TableDatabaseError(f"Failed to create table: {e}"))


def get_table(conn: sqlite3.Connection, table_id: TableId) -> Result[Table, TableError]:
    """Get a table by ID from the database.

    Args:
        conn: Database connection.
        table_id: ID of the table to retrieve.

    Returns:
        Success with Table, or Failure with TableNotFoundError or TableDatabaseError.
    """
    try:
        cursor = conn.execute(
            """
            SELECT id, question, context, status, version, created_at, updated_at, creator_patron_id
            FROM tables WHERE id = ?
            """,
            (table_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return Failure(TableNotFoundError(table_id))

        return Success(_row_to_table(row))
    except sqlite3.Error as e:
        return Failure(TableDatabaseError(f"Failed to get table: {e}"))


# @shell_complexity: 5 branches for optimistic concurrency check + replace-only patch + state machine transition + error paths
def update_table(
    conn: sqlite3.Connection,
    table_id: TableId,
    update: TableUpdate,
    expected_version: Version,
    now: datetime,
) -> Result[Table, TableError]:
    """Update a table with optimistic concurrency control.

    REPLACE-ONLY SEMANTICS: The update replaces all user-modifiable fields.
    This is NOT a partial patch - the update must contain all fields.

    Optimistic Concurrency:
    - Client provides expected_version (the version they last saw)
    - Server checks that current_version == expected_version
    - If mismatch, raises VersionConflictError
    - If match, applies update and increments version

    Args:
        conn: Database connection.
        table_id: ID of the table to update.
        update: Replacement data (must contain all updatable fields).
        expected_version: Version the client expects (optimistic concurrency).
        now: Current timestamp for updated_at.

    Returns:
        Success with updated Table, or Failure with TableError.

    Example VersionConflictError JSON:
        >>> # Simulated scenario
        >>> error = VersionConflictError(TableId("table-123"), Version(5), Version(3))
        >>> error.to_json()
        {'error': 'version_conflict', 'table_id': 'table-123', 'current_version': 5, 'expected_version': 3, 'message': 'Version conflict for table table-123: expected version 3, but current is 5'}
    """
    # Step 1: Get current table
    get_result = get_table(conn, table_id)
    if isinstance(get_result, Failure):
        return get_result

    current_table = get_result.unwrap()

    # Step 2: Prepare versioned update (validates version, creates new table)
    # This is pure logic - no I/O
    try:
        updated_table = prepare_versioned_update(current_table, update, expected_version, now)
    except VersionMismatchError as e:
        return Failure(VersionConflictError(table_id, e.current_version, e.expected_version))

    # Step 3: Apply update to database
    # Use version in WHERE clause for additional safety (atomic check)
    try:
        cursor = conn.execute(
            """
            UPDATE tables
            SET question = ?, context = ?, status = ?, version = ?, updated_at = ?
            WHERE id = ? AND version = ?
            """,
            (
                updated_table.question,
                updated_table.context,
                updated_table.status.value,
                updated_table.version,
                updated_table.updated_at.isoformat(),
                table_id,
                expected_version,  # Extra safety: only update if version matches
            ),
        )
        conn.commit()

        # Check if the update actually happened
        if cursor.rowcount == 0:
            # This could happen if version changed between our read and write
            # (race condition in concurrent environment)
            # Re-read to get current version for error message
            reget_result = get_table(conn, table_id)
            if isinstance(reget_result, Failure):
                return Failure(TableDatabaseError("Race condition detected during update"))

            actual = reget_result.unwrap()
            return Failure(VersionConflictError(table_id, actual.version, expected_version))

        return Success(updated_table)
    except sqlite3.Error as e:
        return Failure(TableDatabaseError(f"Failed to update table: {e}"))


def list_tables(conn: sqlite3.Connection) -> Result[list[Table], TableError]:
    """List all tables from the database.

    Args:
        conn: Database connection.

    Returns:
        Success with list of tables (may be empty), or Failure with TableError.
    """
    try:
        cursor = conn.execute(
            """
            SELECT id, question, context, status, version, created_at, updated_at, creator_patron_id
            FROM tables
            ORDER BY created_at DESC
            """
        )
        rows = cursor.fetchall()
        tables = [_row_to_table(row) for row in rows]
        return Success(tables)
    except sqlite3.Error as e:
        return Failure(TableDatabaseError(f"Failed to list tables: {e}"))


def list_tables_with_seat_counts(
    conn: sqlite3.Connection,
    ttl_seconds: int,
    now: datetime,
) -> Result[list[dict], TableError]:
    """List all open tables with active seat counts.

    Queries all tables with status='open' and joins seats to compute
    active_count per table (excluding expired seats based on TTL).

    Args:
        conn: Database connection.
        ttl_seconds: Time-to-live in seconds for seat expiry calculation.
        now: Current timestamp for expiry calculation.

    Returns:
        Success with list of dicts containing:
            - id: Table ID
            - question: Table question/topic
            - context: Optional context
            - status: Table status (will always be 'open')
            - version: Version number for optimistic concurrency
            - created_at: Creation timestamp (ISO format string)
            - updated_at: Last update timestamp (ISO format string)
            - active_count: Number of active (non-expired) seats

    Example:
        >>> import sqlite3
        >>> from datetime import datetime
        >>> from tasca.shell.storage.seat_repo import create_seats_table
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = create_tables_table(conn)  # Setup tables schema
        >>> _ = create_seats_table(conn)   # Setup seats schema (needed for JOIN)
        >>> result = list_tables_with_seat_counts(conn, 300, datetime.now())
        >>> isinstance(result, Success)
        True
        >>> result.unwrap()  # Empty list since no tables exist
        []
    """
    from datetime import timedelta

    try:
        # Compute the cutoff ISO string: seats with last_heartbeat + ttl >= now are active.
        # A JOINED seat is expired when: now > last_heartbeat + ttl_seconds
        # A LEFT seat is never expired (always counts as active).
        # We mirror filter_active_seats / is_seat_expired semantics exactly in SQL.
        cutoff = (now - timedelta(seconds=ttl_seconds)).isoformat()

        # Single JOIN query: COUNT only active seats per open table.
        # Active seat condition (matching is_seat_expired logic):
        #   state = 'left'  → always active (LEFT seats are never expired)
        #   state = 'joined' AND last_heartbeat >= cutoff  → within TTL, not expired
        #   state = 'joined' AND last_heartbeat < cutoff   → expired, not counted
        # The cutoff is now - ttl_seconds; a seat with last_heartbeat == cutoff is
        # not expired (expiry requires now > last_heartbeat + ttl, i.e. strict gt).
        cursor = conn.execute(
            """
            SELECT
                t.id,
                t.question,
                t.context,
                t.status,
                t.version,
                t.created_at,
                t.updated_at,
                COUNT(
                    CASE
                        WHEN s.state = 'left' THEN 1
                        WHEN s.state = 'joined'
                             AND s.last_heartbeat >= ?
                        THEN 1
                        ELSE NULL
                    END
                ) AS active_count
            FROM tables t
            LEFT JOIN seats s ON s.table_id = t.id
            WHERE t.status = 'open'
            GROUP BY t.id, t.question, t.context, t.status, t.version, t.created_at, t.updated_at
            ORDER BY t.created_at DESC
            """,
            (cutoff,),
        )
        rows = cursor.fetchall()

        result_tables: list[dict] = [
            {
                "id": row[0],
                "question": row[1],
                "context": row[2],
                "status": row[3],
                "version": row[4],
                "created_at": row[5],
                "updated_at": row[6],
                "active_count": row[7],
            }
            for row in rows
        ]

        return Success(result_tables)
    except sqlite3.Error as e:
        return Failure(TableDatabaseError(f"Failed to list tables with seat counts: {e}"))


def delete_table(conn: sqlite3.Connection, table_id: TableId) -> Result[None, TableError]:
    """Delete a table by ID.

    Args:
        conn: Database connection.
        table_id: ID of the table to delete.

    Returns:
        Success(None) if deleted, or Failure with TableError.
    """
    try:
        cursor = conn.execute("DELETE FROM tables WHERE id = ?", (table_id,))
        conn.commit()
        if cursor.rowcount == 0:
            return Failure(TableNotFoundError(table_id))
        return Success(None)
    except sqlite3.Error as e:
        return Failure(TableDatabaseError(f"Failed to delete table: {e}"))


# =============================================================================
# Helper to create schema (for testing)
# =============================================================================


def create_tables_table(conn: sqlite3.Connection) -> Result[None, TableError]:
    """Create the tables table if it doesn't exist.

    Args:
        conn: Database connection.

    Returns:
        Success(None) or Failure with TableError.
    """
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tables (
                id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                context TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                creator_patron_id TEXT
            )
        """)
        conn.commit()
        return Success(None)
    except sqlite3.Error as e:
        return Failure(TableDatabaseError(f"Failed to create tables table: {e}"))
