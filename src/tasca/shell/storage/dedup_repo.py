"""
Deduplication repository - SQLite implementation for content deduplication.

This module handles I/O operations for deduplication, including:
- Checking for duplicate content via content hash
- Storing new content hashes with previews
- return_existing semantics: Return existing record on duplicate

All database operations use Result[T, E] for error handling.
"""

import sqlite3
from datetime import datetime, timezone
from typing import NewType

from pydantic import BaseModel
from returns.result import Failure, Result, Success

from tasca.core.services.dedup_service import compute_hash_and_preview

# Type for repository errors
DedupError = NewType("DedupError", str)


class DedupRecord(BaseModel):
    """A deduplication record for tracking content.

    Attributes:
        content_hash: SHA-256 hash of the content (primary key).
        content_preview: Truncated preview for display.
        first_seen_at: Timestamp when content was first seen.
    """

    content_hash: str
    content_preview: str
    first_seen_at: datetime


# @shell_orchestration: Repository operation with Result type
def check_duplicate(
    conn: sqlite3.Connection, content_hash: str
) -> Result[DedupRecord | None, DedupError]:
    """Check if content hash already exists in dedup table.

    Args:
        conn: Database connection.
        content_hash: SHA-256 hash of the content.

    Returns:
        Success with DedupRecord if found, Success(None) if not found,
        or Failure with error.

    Example:
        >>> from tasca.shell.storage.database import apply_schema
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> result = check_duplicate(conn, "nonexistent")
        >>> isinstance(result, Success) and result.unwrap() is None
        True
        >>> conn.close()
    """
    try:
        cursor = conn.execute(
            """
            SELECT content_hash, content_preview, first_seen_at
            FROM dedup
            WHERE content_hash = ?
            """,
            (content_hash,),
        )
        row = cursor.fetchone()

        if not row:
            return Success(None)

        record = _row_to_dedup_record(row)
        return Success(record)

    except sqlite3.Error as e:
        return Failure(DedupError(f"Database error: {e}"))


# @shell_orchestration: Repository operation with Result type
# @shell_complexity: Multi-step operation with race condition handling (integrity error recovery)
def store_dedup(
    conn: sqlite3.Connection, content_hash: str, content_preview: str
) -> Result[DedupRecord, DedupError]:
    """Store a new dedup record.

    Args:
        conn: Database connection.
        content_hash: SHA-256 hash of the content.
        content_preview: Truncated preview for display.

    Returns:
        Success with created DedupRecord, or Failure with error.

    Raises:
        IntegrityError (caught): If content_hash already exists.

    Example:
        >>> from tasca.shell.storage.database import apply_schema
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> result = store_dedup(conn, "a" * 64, "Hello...")
        >>> isinstance(result, Success)
        True
        >>> record = result.unwrap()
        >>> record.content_hash == "a" * 64
        True
        >>> conn.close()
    """
    try:
        now = datetime.now(timezone.utc)
        now_str = now.isoformat()

        conn.execute(
            """
            INSERT INTO dedup (content_hash, content_preview, first_seen_at)
            VALUES (?, ?, ?)
            """,
            (content_hash, content_preview, now_str),
        )
        conn.commit()

        return Success(
            DedupRecord(
                content_hash=content_hash,
                content_preview=content_preview,
                first_seen_at=now,
            )
        )

    except sqlite3.IntegrityError:
        # Race condition: another thread inserted the same hash
        # Return the existing record
        existing_result = check_duplicate(conn, content_hash)
        if isinstance(existing_result, Failure):
            return existing_result
        existing = existing_result.unwrap()
        if existing is not None:
            return Success(existing)
        # This should not happen with proper PRIMARY KEY constraint
        # but handle gracefully by returning a constructed record
        return Failure(
            DedupError(f"IntegrityError but no existing record found for hash: {content_hash}")
        )
    except sqlite3.Error as e:
        return Failure(DedupError(f"Database error: {e}"))


# @shell_orchestration: Combined operation with return_existing semantics
def store_or_get_existing(
    conn: sqlite3.Connection, content: str
) -> Result[tuple[DedupRecord, bool], DedupError]:
    """Store content hash or return existing record (return_existing semantics).

    This is the main dedup operation with return_existing semantics:
    - Computes content hash and preview
    - Checks if hash already exists
    - If exists: returns existing record with is_new=False
    - If not exists: stores new record and returns with is_new=True

    Args:
        conn: Database connection.
        content: Content string to deduplicate.

    Returns:
        Success with (DedupRecord, is_new) tuple:
        - is_new=True: New record was created
        - is_new=False: Existing record was returned
        Or Failure with error.

    Example:
        >>> from tasca.shell.storage.database import apply_schema
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> # First call - creates new record
        >>> result1 = store_or_get_existing(conn, "Hello, world!")
        >>> isinstance(result1, Success)
        True
        >>> record1, is_new1 = result1.unwrap()
        >>> is_new1
        True
        >>> # Second call - returns existing
        >>> result2 = store_or_get_existing(conn, "Hello, world!")
        >>> isinstance(result2, Success)
        True
        >>> record2, is_new2 = result2.unwrap()
        >>> is_new2
        False
        >>> record2.content_hash == record1.content_hash
        True
        >>> conn.close()
    """
    # Compute hash and preview (pure function from core)
    content_hash, content_preview = compute_hash_and_preview(content)

    # Check for existing record
    existing_result = check_duplicate(conn, content_hash)
    if isinstance(existing_result, Failure):
        return existing_result

    existing = existing_result.unwrap()
    if existing is not None:
        # Return existing record with is_new=False
        return Success((existing, False))

    # Store new record
    store_result = store_dedup(conn, content_hash, content_preview)
    if isinstance(store_result, Failure):
        return store_result

    # Return new record with is_new=True
    return Success((store_result.unwrap(), True))


# @invar:allow shell_result: Private helper converting DB row to domain object
# @shell_orchestration: Helper for row-to-domain conversion within repository
def _row_to_dedup_record(row: tuple) -> DedupRecord:
    """Convert a database row to a DedupRecord domain object.

    Args:
        row: Database row tuple (content_hash, content_preview, first_seen_at).

    Returns:
        DedupRecord domain object.
    """
    content_hash, content_preview, first_seen_at_str = row
    first_seen_at = datetime.fromisoformat(first_seen_at_str)

    return DedupRecord(
        content_hash=content_hash,
        content_preview=content_preview,
        first_seen_at=first_seen_at,
    )
