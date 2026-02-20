"""
Seat repository - SQLite implementation for seat persistence and GC.

This module provides I/O operations for seats including heartbeat updates
and garbage collection of expired seats.
Shell layer - handles I/O (database operations) and returns Result[T, E].
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from returns.result import Failure, Result, Success

from tasca.core.domain.seat import Seat, SeatId, SeatState
from tasca.core.services.seat_service import filter_active_seats, is_seat_expired


# =============================================================================
# Error Types
# =============================================================================


class SeatError(Exception):
    """Base error for seat operations."""

    pass


class SeatNotFoundError(SeatError):
    """Seat not found in database."""

    def __init__(self, seat_id: SeatId) -> None:
        self.seat_id = seat_id
        super().__init__(f"Seat not found: {seat_id}")


class SeatDatabaseError(SeatError):
    """Database error during seat operation."""

    pass


# =============================================================================
# Repository Operations
# =============================================================================


# @invar:allow shell_result: Private helper - pure data transformation, not a shell operation
# @shell_orchestration: Helper for row-to-domain mapping, used internally by repo functions
def _row_to_seat(row: tuple) -> Seat:
    """Convert a database row to a Seat object."""
    return Seat(
        id=SeatId(row[0]),
        table_id=row[1],
        patron_id=row[2],
        state=SeatState(row[3]),
        last_heartbeat=datetime.fromisoformat(row[4]),
        joined_at=datetime.fromisoformat(row[5]),
    )


def create_seat(conn: sqlite3.Connection, seat: Seat) -> Result[Seat, SeatError]:
    """Create a new seat in the database.

    Args:
        conn: Database connection.
        seat: Seat to create.

    Returns:
        Success with the created Seat, or Failure with SeatError.
    """
    try:
        conn.execute(
            """
            INSERT INTO seats (id, table_id, patron_id, state, last_heartbeat, joined_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                seat.id,
                seat.table_id,
                seat.patron_id,
                seat.state.value,
                seat.last_heartbeat.isoformat(),
                seat.joined_at.isoformat(),
            ),
        )
        conn.commit()
        return Success(seat)
    except sqlite3.IntegrityError as e:
        return Failure(SeatError(f"Seat already exists or constraint violation: {e}"))
    except sqlite3.Error as e:
        return Failure(SeatDatabaseError(f"Failed to create seat: {e}"))


def get_seat(conn: sqlite3.Connection, seat_id: SeatId) -> Result[Seat, SeatError]:
    """Get a seat by ID from the database.

    Args:
        conn: Database connection.
        seat_id: ID of the seat to retrieve.

    Returns:
        Success with Seat, or Failure with SeatNotFoundError or SeatDatabaseError.
    """
    try:
        cursor = conn.execute(
            """
            SELECT id, table_id, patron_id, state, last_heartbeat, joined_at
            FROM seats WHERE id = ?
            """,
            (seat_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return Failure(SeatNotFoundError(seat_id))

        return Success(_row_to_seat(row))
    except sqlite3.Error as e:
        return Failure(SeatDatabaseError(f"Failed to get seat: {e}"))


def heartbeat_seat(
    conn: sqlite3.Connection, seat_id: SeatId, now: datetime
) -> Result[Seat, SeatError]:
    """Update a seat's last_heartbeat timestamp.

    This is the heartbeat mechanism - seats periodically update their
    last_heartbeat to indicate they are still active.

    Args:
        conn: Database connection.
        seat_id: ID of the seat to update.
        now: Current timestamp for the heartbeat.

    Returns:
        Success with updated Seat, or Failure with SeatError.
    """
    try:
        # First get the seat to verify it exists
        get_result = get_seat(conn, seat_id)
        if isinstance(get_result, Failure):
            return get_result

        seat = get_result.unwrap()

        # Update the heartbeat
        conn.execute(
            """
            UPDATE seats SET last_heartbeat = ? WHERE id = ?
            """,
            (now.isoformat(), seat_id),
        )
        conn.commit()

        return Success(seat.model_copy(update={"last_heartbeat": now}))
    except sqlite3.Error as e:
        return Failure(SeatDatabaseError(f"Failed to heartbeat seat: {e}"))


def find_seats_by_table(conn: sqlite3.Connection, table_id: str) -> Result[list[Seat], SeatError]:
    """Find all seats for a given table.

    Args:
        conn: Database connection.
        table_id: ID of the table.

    Returns:
        Success with list of seats (may be empty), or Failure with SeatError.
    """
    try:
        cursor = conn.execute(
            """
            SELECT id, table_id, patron_id, state, last_heartbeat, joined_at
            FROM seats WHERE table_id = ?
            """,
            (table_id,),
        )
        rows = cursor.fetchall()
        seats = [_row_to_seat(row) for row in rows]
        return Success(seats)
    except sqlite3.Error as e:
        return Failure(SeatDatabaseError(f"Failed to find seats by table: {e}"))


def find_expired_seats(
    conn: sqlite3.Connection, ttl_seconds: int, now: datetime
) -> Result[list[Seat], SeatError]:
    """Find all expired seats across all tables.

    This is the GC discovery query - finds seats whose heartbeat is stale.
    Only seats in JOINED state can be expired.

    Args:
        conn: Database connection.
        ttl_seconds: Time-to-live in seconds.
        now: Current timestamp.

    Returns:
        Success with list of expired seats, or Failure with SeatError.
    """
    try:
        from datetime import timedelta

        # Calculate the cutoff time
        cutoff = now - timedelta(seconds=ttl_seconds)
        cutoff_iso = cutoff.isoformat()

        # Find seats that are JOINED and have stale heartbeat
        cursor = conn.execute(
            """
            SELECT id, table_id, patron_id, state, last_heartbeat, joined_at
            FROM seats
            WHERE state = 'joined'
            AND datetime(last_heartbeat) < datetime(?)
            """,
            (cutoff_iso,),
        )
        rows = cursor.fetchall()

        seats = [_row_to_seat(row) for row in rows]

        # Double-check with core logic (belt and suspenders)
        # This ensures consistency even if SQL query has edge cases
        expired = [s for s in seats if is_seat_expired(s, ttl_seconds, now)]
        return Success(expired)
    except sqlite3.Error as e:
        return Failure(SeatDatabaseError(f"Failed to find expired seats: {e}"))


def delete_seat(conn: sqlite3.Connection, seat_id: SeatId) -> Result[None, SeatError]:
    """Delete a seat by ID.

    Args:
        conn: Database connection.
        seat_id: ID of the seat to delete.

    Returns:
        Success(None) if deleted, or Failure with SeatError.
    """
    try:
        cursor = conn.execute("DELETE FROM seats WHERE id = ?", (seat_id,))
        conn.commit()
        if cursor.rowcount == 0:
            return Failure(SeatNotFoundError(seat_id))
        return Success(None)
    except sqlite3.Error as e:
        return Failure(SeatDatabaseError(f"Failed to delete seat: {e}"))


def delete_seats(conn: sqlite3.Connection, seat_ids: list[SeatId]) -> Result[int, SeatError]:
    """Delete multiple seats by ID.

    Args:
        conn: Database connection.
        seat_ids: List of seat IDs to delete.

    Returns:
        Success with count of deleted seats, or Failure with SeatError.
    """
    if not seat_ids:
        return Success(0)

    try:
        placeholders = ",".join("?" * len(seat_ids))
        cursor = conn.execute(
            f"DELETE FROM seats WHERE id IN ({placeholders})",
            tuple(seat_ids),
        )
        conn.commit()
        return Success(cursor.rowcount)
    except sqlite3.Error as e:
        return Failure(SeatDatabaseError(f"Failed to delete seats: {e}"))


def gc_expired_seats(
    conn: sqlite3.Connection, ttl_seconds: int, now: datetime
) -> Result[int, SeatError]:
    """Garbage collect expired seats.

    This is the GC cleanup operation - finds and deletes all expired seats.
    Uses a single transaction for atomicity.

    Args:
        conn: Database connection.
        ttl_seconds: Time-to-live in seconds.
        now: Current timestamp.

    Returns:
        Success with count of deleted seats, or Failure with SeatError.
    """
    # Find expired seats
    find_result = find_expired_seats(conn, ttl_seconds, now)
    if isinstance(find_result, Failure):
        return find_result

    expired_seats = find_result.unwrap()
    if not expired_seats:
        return Success(0)

    # Delete them
    seat_ids = [s.id for s in expired_seats]
    return delete_seats(conn, seat_ids)


def count_active_seats(
    conn: sqlite3.Connection, table_id: str, ttl_seconds: int, now: datetime
) -> Result[int, SeatError]:
    """Count active (non-expired) seats at a table.

    Args:
        conn: Database connection.
        table_id: ID of the table.
        ttl_seconds: Time-to-live in seconds.
        now: Current timestamp.

    Returns:
        Success with count of active seats, or Failure with SeatError.
    """
    find_result = find_seats_by_table(conn, table_id)
    if isinstance(find_result, Failure):
        return find_result

    seats = find_result.unwrap()
    active = filter_active_seats(seats, ttl_seconds, now)
    return Success(len(active))


# =============================================================================
# Helper to create schema (for testing)
# =============================================================================


def create_seats_table(conn: sqlite3.Connection) -> Result[None, SeatError]:
    """Create the seats table if it doesn't exist.

    Args:
        conn: Database connection.

    Returns:
        Success(None) or Failure with SeatError.
    """
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seats (
                id TEXT PRIMARY KEY,
                table_id TEXT NOT NULL,
                patron_id TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'joined',
                last_heartbeat TEXT NOT NULL,
                joined_at TEXT NOT NULL
            )
        """)
        conn.commit()
        return Success(None)
    except sqlite3.Error as e:
        return Failure(SeatDatabaseError(f"Failed to create seats table: {e}"))
