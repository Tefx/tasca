"""
Saying repository - SQLite implementation for saying persistence.

This module handles I/O operations for sayings, including:
- Atomic sequence allocation via SQLite transactions
- CRUD operations for sayings
- Querying sayings by table and sequence range

All database operations use Result[T, E] for error handling.
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import NewType

from returns.result import Failure, Result, Success

from tasca.core.domain.patron import PatronId
from tasca.core.domain.saying import Saying, SayingId, Speaker, SpeakerKind
from tasca.core.services.saying_service import compute_next_sequence, get_max_sequence

# Type for repository errors
SayingError = NewType("SayingError", str)


# @shell_orchestration: Multi-step operation with transaction
# @shell_complexity: Multi-step atomic operation (lock, query, compute, insert)
# Transaction boundary guarantees atomicity - cannot decompose further
def append_saying(
    conn: sqlite3.Connection,
    table_id: str,
    speaker: Speaker,
    content: str,
) -> Result[Saying, SayingError]:
    """Atomically allocate sequence and insert a new saying.

    This operation is atomic:
    1. Get current max sequence for table (locked)
    2. Compute next sequence
    3. Insert saying with new sequence
    4. All in one transaction

    Args:
        conn: Database connection (must have transaction support).
        table_id: UUID of the table.
        speaker: Speaker information.
        content: Markdown content of the saying.

    Returns:
        Success with the created Saying, or Failure with error message.

    Note:
        The UNIQUE(table_id, sequence) constraint in the schema guarantees
        no duplicate sequences can exist for the same table.
    """
    try:
        # Generate saying ID
        saying_id = SayingId(str(uuid.uuid4()))
        now = datetime.now(timezone.utc)

        cursor = conn.cursor()

        # Atomic: Get max sequence and insert in one transaction
        # SQLite DEFAULT transaction behavior is DEFERRED, which means
        # the transaction starts on the first write operation.
        # For atomicity, we use explicit BEGIN IMMEDIATE to acquire write lock.

        cursor.execute("BEGIN IMMEDIATE")

        try:
            # Get current max sequence for this table
            cursor.execute(
                "SELECT COALESCE(MAX(sequence), -1) FROM sayings WHERE table_id = ?",
                (table_id,),
            )
            row = cursor.fetchone()
            current_max = int(row[0]) if row else -1

            # Compute next sequence (pure function from core)
            next_sequence = compute_next_sequence(current_max)

            # Insert the saying
            cursor.execute(
                """
                INSERT INTO sayings (
                    id, table_id, sequence, speaker_kind, speaker_name,
                    patron_id, content, pinned, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    saying_id,
                    table_id,
                    next_sequence,
                    speaker.kind.value,
                    speaker.name,
                    speaker.patron_id,
                    content,
                    0,  # pinned defaults to False
                    now.isoformat(),
                ),
            )

            conn.commit()

            return Success(
                Saying(
                    id=saying_id,
                    table_id=table_id,
                    sequence=next_sequence,
                    speaker=speaker,
                    content=content,
                    pinned=False,
                    created_at=now,
                )
            )

        except sqlite3.IntegrityError as e:
            conn.rollback()
            # This should never happen with proper transaction handling,
            # but the UNIQUE constraint provides a safety net
            error_msg = str(e).lower()
            if "unique" in error_msg:
                return Failure(
                    SayingError(
                        f"Sequence conflict: duplicate (table_id, sequence) for table {table_id}"
                    )
                )
            return Failure(SayingError(f"Integrity error: {e}"))

    except sqlite3.Error as e:
        return Failure(SayingError(f"Database error: {e}"))


def get_saying_by_id(
    conn: sqlite3.Connection, saying_id: str
) -> Result[Saying | None, SayingError]:
    """Get a saying by its ID.

    Args:
        conn: Database connection.
        saying_id: UUID of the saying.

    Returns:
        Success with Saying if found, Success(None) if not found,
        or Failure with error.
    """
    try:
        cursor = conn.execute(
            """
            SELECT id, table_id, sequence, speaker_kind, speaker_name,
                   patron_id, content, pinned, created_at
            FROM sayings WHERE id = ?
            """,
            (saying_id,),
        )
        row = cursor.fetchone()

        if not row:
            return Success(None)

        saying = _row_to_saying(row)
        return Success(saying)

    except sqlite3.Error as e:
        return Failure(SayingError(f"Database error: {e}"))


def get_saying_by_sequence(
    conn: sqlite3.Connection, table_id: str, sequence: int
) -> Result[Saying | None, SayingError]:
    """Get a saying by table_id and sequence.

    Args:
        conn: Database connection.
        table_id: UUID of the table.
        sequence: Sequence number within the table.

    Returns:
        Success with Saying if found, Success(None) if not found,
        or Failure with error.
    """
    try:
        cursor = conn.execute(
            """
            SELECT id, table_id, sequence, speaker_kind, speaker_name,
                   patron_id, content, pinned, created_at
            FROM sayings WHERE table_id = ? AND sequence = ?
            """,
            (table_id, sequence),
        )
        row = cursor.fetchone()

        if not row:
            return Success(None)

        saying = _row_to_saying(row)
        return Success(saying)

    except sqlite3.Error as e:
        return Failure(SayingError(f"Database error: {e}"))


def list_sayings_by_table(
    conn: sqlite3.Connection,
    table_id: str,
    since_sequence: int = -1,
    limit: int = 50,
) -> Result[list[Saying], SayingError]:
    """List sayings for a table, optionally after a sequence.

    Args:
        conn: Database connection.
        table_id: UUID of the table.
        since_sequence: Get sayings with sequence > this value (-1 for all).
        limit: Maximum number of sayings to return.

    Returns:
        Success with list of Sayings (ordered by sequence),
        or Failure with error.
    """
    try:
        cursor = conn.execute(
            """
            SELECT id, table_id, sequence, speaker_kind, speaker_name,
                   patron_id, content, pinned, created_at
            FROM sayings
            WHERE table_id = ? AND sequence > ?
            ORDER BY sequence ASC
            LIMIT ?
            """,
            (table_id, since_sequence, limit),
        )
        rows = cursor.fetchall()

        sayings = [_row_to_saying(row) for row in rows]
        return Success(sayings)

    except sqlite3.Error as e:
        return Failure(SayingError(f"Database error: {e}"))


# @shell_complexity: 5 branches for count query + row iteration + byte limit check + empty result + has_more detection
def get_recent_sayings(
    conn: sqlite3.Connection,
    table_id: str,
    limit: int = 10,
    max_bytes: int = 65536,
) -> Result[tuple[list[Saying], int, bool], SayingError]:
    """Get the most recent sayings for a table with byte limit.

    Returns sayings in reverse order (newest first), applying both
    count and byte limits. Used for initial history window in table.join.

    Args:
        conn: Database connection.
        table_id: UUID of the table.
        limit: Maximum number of sayings to return (default 10).
        max_bytes: Maximum total bytes of content (default 65536).

    Returns:
        Success with tuple of (sayings, history_sequence, has_more):
        - sayings: List of recent sayings (oldest first, ready for display)
        - history_sequence: The sequence before the oldest returned saying
          (for paging older history)
        - has_more: True if there are older sayings beyond the returned window
        Or Failure with error.
    """
    try:
        # Get total count to determine if there are older sayings
        cursor = conn.execute(
            "SELECT COUNT(*) FROM sayings WHERE table_id = ?",
            (table_id,),
        )
        total_row = cursor.fetchone()
        total_count = int(total_row[0]) if total_row else 0

        # Get recent sayings in descending order (newest first)
        # Fetch more than limit to check for has_more
        cursor = conn.execute(
            """
            SELECT id, table_id, sequence, speaker_kind, speaker_name,
                   patron_id, content, pinned, created_at
            FROM sayings
            WHERE table_id = ?
            ORDER BY sequence DESC
            LIMIT ?
            """,
            (table_id, limit + 1),  # Fetch one extra to check for has_more
        )
        rows = cursor.fetchall()

        # Apply byte limit while collecting sayings
        sayings: list[Saying] = []
        total_bytes = 0
        has_more_by_count = len(rows) > limit

        for row in rows[:limit]:  # Only consider up to limit
            saying = _row_to_saying(row)
            content_bytes = len(saying.content.encode("utf-8"))

            if total_bytes + content_bytes > max_bytes and sayings:
                # Would exceed byte limit and we have at least one saying
                # Stop here - we have history (may have more)
                break

            sayings.append(saying)
            total_bytes += content_bytes

        # Check if there are more sayings beyond what we returned
        # Either by count limit or by what's actually in DB
        if not sayings:
            # No sayings at all
            return Success(([], -1, False))

        # Reverse to get oldest-first order
        sayings.reverse()

        # history_sequence is the sequence before the oldest returned saying
        # This is what clients use to page older history
        oldest_sequence = sayings[0].sequence
        history_sequence = oldest_sequence - 1

        # has_more: are there sayings older than the oldest we returned?
        has_more = oldest_sequence > 0 or (total_count > len(sayings))

        # More precise check: is there a saying with sequence < oldest_sequence?
        cursor = conn.execute(
            "SELECT 1 FROM sayings WHERE table_id = ? AND sequence < ? LIMIT 1",
            (table_id, oldest_sequence),
        )
        older_exists = cursor.fetchone() is not None

        return Success((sayings, history_sequence, older_exists))

    except sqlite3.Error as e:
        return Failure(SayingError(f"Database error: {e}"))


# Default max bytes for export operations (100 MiB)
# This prevents OOM on extremely large tables while still allowing all practical exports
DEFAULT_EXPORT_MAX_BYTES = 100 * 1024 * 1024  # 100 MiB


# @shell_complexity: 4 branches for byte check + database query + error handling
# @invar:allow shell_result: Shell layer - database I/O
def list_all_sayings_by_table(
    conn: sqlite3.Connection,
    table_id: str,
    max_bytes: int = DEFAULT_EXPORT_MAX_BYTES,
) -> Result[list[Saying], SayingError]:
    """List ALL sayings for a table for export WITHOUT count truncation.

    This is the export path function - it does NOT truncate by count.
    Memory safety is provided by max_bytes limit.

    For tables exceeding max_bytes, returns a Failure with an error
    indicating the table is too large to export.

    Args:
        conn: Database connection.
        table_id: UUID of the table.
        max_bytes: Maximum total bytes of content (default 100 MiB).
            Set to 0 or negative to disable byte limit.

    Returns:
        Success with list of ALL Sayings (ordered by sequence),
        or Failure with error (including size exceeded).

    Raises:
        No exceptions raised - errors return Failure.
    """
    try:
        # First, check if the table content exceeds max_bytes
        if max_bytes > 0:
            cursor = conn.execute(
                "SELECT COALESCE(SUM(LENGTH(content)), 0) FROM sayings WHERE table_id = ?",
                (table_id,),
            )
            row = cursor.fetchone()
            total_chars = int(row[0]) if row else 0

            # Estimate bytes (UTF-8 can be up to 4 bytes per char, but usually 1-2)
            # Use 2x as a reasonable upper bound estimate
            estimated_bytes = total_chars * 2

            if estimated_bytes > max_bytes:
                return Failure(
                    SayingError(
                        f"Export size exceeded: table has ~{estimated_bytes // (1024 * 1024)} MiB "
                        f"of content (limit: {max_bytes // (1024 * 1024)} MiB). "
                        f"Use a larger max_bytes limit if needed."
                    )
                )

        # Fetch ALL sayings without count limit
        cursor = conn.execute(
            """
            SELECT id, table_id, sequence, speaker_kind, speaker_name,
                   patron_id, content, pinned, created_at
            FROM sayings
            WHERE table_id = ?
            ORDER BY sequence ASC
            """,
            (table_id,),
        )
        rows = cursor.fetchall()

        sayings = [_row_to_saying(row) for row in rows]
        return Success(sayings)

    except sqlite3.Error as e:
        return Failure(SayingError(f"Database error: {e}"))


def get_table_max_sequence(conn: sqlite3.Connection, table_id: str) -> Result[int, SayingError]:
    """Get the maximum sequence for a table.

    Args:
        conn: Database connection (must have sayings table).
        table_id: UUID of the table.

    Returns:
        Success with max sequence (-1 if no sayings exist),
        or Failure with error.

    Example (requires schema):
        >>> from tasca.shell.storage.database import apply_schema
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)  # Create schema
        >>> get_table_max_sequence(conn, "table-001").unwrap()
        -1
    """
    try:
        cursor = conn.execute(
            "SELECT COALESCE(MAX(sequence), -1) FROM sayings WHERE table_id = ?",
            (table_id,),
        )
        row = cursor.fetchone()
        max_seq = int(row[0]) if row else -1
        return Success(max_seq)

    except sqlite3.Error as e:
        return Failure(SayingError(f"Database error: {e}"))


def count_sayings_by_table(conn: sqlite3.Connection, table_id: str) -> Result[int, SayingError]:
    """Count sayings for a table.

    Args:
        conn: Database connection.
        table_id: UUID of the table.

    Returns:
        Success with count, or Failure with error.
    """
    try:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM sayings WHERE table_id = ?",
            (table_id,),
        )
        row = cursor.fetchone()
        count = int(row[0]) if row else 0
        return Success(count)

    except sqlite3.Error as e:
        return Failure(SayingError(f"Database error: {e}"))


def get_table_content_bytes(conn: sqlite3.Connection, table_id: str) -> Result[int, SayingError]:
    """Get the total byte size of all content in a table.

    This calculates the total bytes of all saying content in the table,
    useful for bytes limit enforcement.

    Note: SQLite LENGTH() returns characters, not bytes. For ASCII content
    this is equivalent, but for Unicode content the actual byte count
    may be higher. For accurate byte counting, content would need to be
    fetched and encoded. This implementation uses character count as a
    reasonable approximation that's efficient at the database level.

    Args:
        conn: Database connection.
        table_id: UUID of the table.

    Returns:
        Success with total bytes estimate (0 if no sayings), or Failure with error.
    """
    try:
        # LENGTH() counts characters, which equals bytes for ASCII
        # For accurate UTF-8 byte count, would need to fetch and encode
        cursor = conn.execute(
            "SELECT COALESCE(SUM(LENGTH(content)), 0) FROM sayings WHERE table_id = ?",
            (table_id,),
        )
        row = cursor.fetchone()
        total = int(row[0]) if row else 0
        return Success(total)

    except sqlite3.Error as e:
        return Failure(SayingError(f"Database error: {e}"))


# @invar:allow shell_result: Private helper converting DB row to domain object
# @shell_orchestration: Helper for row-to-domain conversion within repository
def _row_to_saying(row: tuple) -> Saying:
    """Convert a database row to a Saying domain object.

    Args:
        row: Database row tuple.

    Returns:
        Saying domain object.
    """
    (
        saying_id,
        table_id,
        sequence,
        speaker_kind,
        speaker_name,
        patron_id,
        content,
        pinned,
        created_at_str,
    ) = row

    # Parse the ISO format datetime string
    created_at = datetime.fromisoformat(created_at_str)

    return Saying(
        id=SayingId(saying_id),
        table_id=table_id,
        sequence=sequence,
        speaker=Speaker(
            kind=SpeakerKind(speaker_kind),
            name=speaker_name,
            patron_id=PatronId(patron_id) if patron_id else None,
        ),
        content=content,
        pinned=bool(pinned),
        created_at=created_at,
    )
