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
from datetime import UTC, datetime
from typing import NewType

from returns.result import Failure, Result, Success

from tasca.core.domain.saying import (
    AttachmentId,
    AttachmentInput,
    AttachmentSummary,
    Saying,
    SayingId,
    Speaker,
)
from tasca.core.services.attachment_service import (
    SayingValidationError,
    validate_saying_payload,
)
from tasca.core.services.limits_service import LimitError, LimitsConfig, check_content_limits
from tasca.core.services.saying_service import compute_next_sequence
from tasca.core.storage_rows import row_to_saying
from tasca.shell.storage.attachment_repo import list_attachment_summaries

# Type for repository errors
SayingError = NewType("SayingError", str)


class SayingExportSizeExceededError(Exception):
    """Export content exceeds the configured byte limit."""

    def __init__(self, table_id: str, estimated_bytes: int, max_bytes: int) -> None:
        self.table_id = table_id
        self.estimated_bytes = estimated_bytes
        self.max_bytes = max_bytes
        super().__init__(
            f"Export size exceeded: table has {estimated_bytes} UTF-8 content bytes "
            f"of content (limit: {max_bytes // (1024 * 1024)} MiB). "
            f"Use a larger max_bytes limit if needed."
        )


AppendSayingError = SayingError | LimitError | SayingValidationError


# @shell_complexity: Two aggregate queries normalize empty rows inside the append transaction.
def _read_table_admission_state(
    cursor: sqlite3.Cursor,
    table_id: str,
) -> Result[tuple[int, int, int], SayingError]:
    """Read saying count, max sequence, and total content bytes under the writer lock."""
    try:
        row = cursor.execute(
            """
            SELECT
                COUNT(*),
                COALESCE(MAX(sequence), -1),
                COALESCE(SUM(LENGTH(CAST(content AS BLOB))), 0)
            FROM sayings WHERE table_id = ?
            """,
            (table_id,),
        ).fetchone()
        attachment_row = cursor.execute(
            """
            SELECT COALESCE(SUM(a.byte_size), 0)
            FROM saying_attachments AS a
            JOIN sayings AS s ON s.id = a.saying_id
            WHERE s.table_id = ?
            """,
            (table_id,),
        ).fetchone()
        saying_bytes = int(row[2]) if row else 0
        attachment_bytes = int(attachment_row[0]) if attachment_row else 0
        return Success((
            int(row[0]) if row else 0,
            int(row[1]) if row else -1,
            saying_bytes + attachment_bytes,
        ))
    except sqlite3.Error as exc:
        return Failure(SayingError(f"Database error: {exc}"))


# @shell_orchestration: One BEGIN IMMEDIATE owns limit admission, sequence allocation, and every insert.
# @shell_complexity: Atomic saying + attachment append cannot split its transaction boundary.
def append_saying(
    conn: sqlite3.Connection,
    table_id: str,
    speaker: Speaker,
    content: str,
    attachments: list[AttachmentInput] | None = None,
    limits: LimitsConfig | None = None,
    *,
    manage_transaction: bool = True,
) -> Result[Saying, AppendSayingError]:
    """Atomically validate limits and insert one saying with 0..8 attachments.

    Attachment validation happens before acquiring the writer lock. Configured
    count and table-byte admission, sequence allocation, the saying insert, and
    all attachment inserts happen inside one ``BEGIN IMMEDIATE`` transaction.
    MCP idempotency may supply that transaction so its durable retry record can
    commit or roll back with the saying; all other callers use the default here.
    """
    normalized_attachments = attachments or []
    validation_error = validate_saying_payload(content, normalized_attachments)
    if validation_error is not None:
        return Failure(validation_error)

    attachment_bytes = sum(len(item.content.encode("utf-8")) for item in normalized_attachments)
    effective_limits = limits or LimitsConfig()
    saying_id = SayingId(str(uuid.uuid4()))
    now = datetime.now(UTC)
    cursor = conn.cursor()

    try:
        if manage_transaction:
            cursor.execute("BEGIN IMMEDIATE")
        admission_result = _read_table_admission_state(cursor, table_id)
        if isinstance(admission_result, Failure):
            if manage_transaction:
                conn.rollback()
            return Failure(admission_result.failure())
        current_count, current_max, current_bytes = admission_result.unwrap()
        limit_error = check_content_limits(
            content,
            current_count,
            current_bytes,
            effective_limits,
            attachment_bytes,
        )
        if limit_error is not None:
            if manage_transaction:
                conn.rollback()
            return Failure(limit_error)

        next_sequence = compute_next_sequence(current_max)
        cursor.execute(
            """
            INSERT INTO sayings (
                id, table_id, sequence, speaker_kind, speaker_name,
                patron_id, content, pinned, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                saying_id, table_id, next_sequence, speaker.kind.value,
                speaker.name, speaker.patron_id, content, 0, now.isoformat(),
            ),
        )
        summaries: list[AttachmentSummary] = []
        for position, attachment in enumerate(normalized_attachments):
            attachment_id = AttachmentId(str(uuid.uuid4()))
            byte_size = len(attachment.content.encode("utf-8"))
            cursor.execute(
                """
                INSERT INTO saying_attachments
                    (id, saying_id, position, name, content, byte_size)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (attachment_id, saying_id, position, attachment.name, attachment.content, byte_size),
            )
            summaries.append(AttachmentSummary(
                id=attachment_id,
                name=attachment.name,
                position=position,
                byte_size=byte_size,
            ))
        saying = Saying(
            id=saying_id,
            table_id=table_id,
            sequence=next_sequence,
            speaker=speaker,
            content=content,
            attachments=summaries,
            pinned=False,
            created_at=now,
        )
        if manage_transaction:
            conn.commit()
        return Success(saying)
    except sqlite3.IntegrityError as exc:
        if manage_transaction:
            conn.rollback()
        if "unique" in str(exc).lower():
            return Failure(SayingError(
                f"Sequence or attachment position conflict for table {table_id}"
            ))
        return Failure(SayingError(f"Integrity error: {exc}"))
    except sqlite3.Error as exc:
        if manage_transaction:
            conn.rollback()
        return Failure(SayingError(f"Database error: {exc}"))
    except Exception:
        if manage_transaction:
            conn.rollback()
        raise


def _with_attachment_summaries(
    conn: sqlite3.Connection,
    sayings: list[Saying],
) -> Result[list[Saying], SayingError]:
    """Attach one metadata-only batch query to an already selected saying page."""
    metadata_result = list_attachment_summaries(conn, [str(saying.id) for saying in sayings])
    if isinstance(metadata_result, Failure):
        return Failure(SayingError(str(metadata_result.failure())))
    by_saying = metadata_result.unwrap()
    return Success([
        saying.model_copy(update={"attachments": by_saying.get(str(saying.id), [])})
        for saying in sayings
    ])


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

        summaries_result = _with_attachment_summaries(conn, [row_to_saying(row)])
        if isinstance(summaries_result, Failure):
            return Failure(summaries_result.failure())
        return Success(summaries_result.unwrap()[0])

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

        summaries_result = _with_attachment_summaries(conn, [row_to_saying(row)])
        if isinstance(summaries_result, Failure):
            return Failure(summaries_result.failure())
        return Success(summaries_result.unwrap()[0])

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

        sayings = [row_to_saying(row) for row in rows]
        return _with_attachment_summaries(conn, sayings)

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
        for row in rows[:limit]:  # Only consider up to limit
            saying = row_to_saying(row)
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

        # Reverse to get oldest-first order, then batch-load metadata only.
        sayings.reverse()
        summaries_result = _with_attachment_summaries(conn, sayings)
        if isinstance(summaries_result, Failure):
            return Failure(summaries_result.failure())
        sayings = summaries_result.unwrap()

        # history_sequence is the sequence before the oldest returned saying
        # This is what clients use to page older history
        oldest_sequence = sayings[0].sequence
        history_sequence = oldest_sequence - 1

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
def list_all_sayings_by_table(
    conn: sqlite3.Connection,
    table_id: str,
    max_bytes: int = DEFAULT_EXPORT_MAX_BYTES,
) -> Result[list[Saying], SayingError | SayingExportSizeExceededError]:
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
        # Check exact UTF-8 body + attachment bytes before materializing export content.
        if max_bytes > 0:
            bytes_result = get_table_content_bytes(conn, table_id)
            if isinstance(bytes_result, Failure):
                return Failure(bytes_result.failure())
            content_bytes = bytes_result.unwrap()
            if content_bytes > max_bytes:
                return Failure(SayingExportSizeExceededError(table_id, content_bytes, max_bytes))

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

        sayings = [row_to_saying(row) for row in rows]
        return _with_attachment_summaries(conn, sayings)

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
    """Get exact UTF-8 saying and attachment content bytes for one table.

    SQLite stores these values as UTF-8 text. Casting saying content to BLOB
    makes ``LENGTH`` count bytes; attachments use their validated stored size.

    Args:
        conn: Database connection.
        table_id: UUID of the table.

    Returns:
        Success with the exact total (0 if no sayings), or Failure with error.
    """
    try:
        cursor = conn.execute(
            """
            SELECT
                COALESCE(SUM(LENGTH(CAST(s.content AS BLOB))), 0)
                + COALESCE((
                    SELECT SUM(a.byte_size)
                    FROM saying_attachments AS a
                    JOIN sayings AS attached_saying ON attached_saying.id = a.saying_id
                    WHERE attached_saying.table_id = ?
                ), 0)
            FROM sayings AS s
            WHERE s.table_id = ?
            """,
            (table_id, table_id),
        )
        row = cursor.fetchone()
        total = int(row[0]) if row else 0
        return Success(total)

    except sqlite3.Error as e:
        return Failure(SayingError(f"Database error: {e}"))
