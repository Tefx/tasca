"""
Deduplication repository - SQLite implementation for content deduplication.

This module handles I/O operations for deduplication, including:
- Checking for duplicate content via content hash
- Storing new content hashes with previews
- return_existing semantics: Return existing record on duplicate
- Expiry-aware operations: Expired entries behave as misses
- Periodic cleanup: Batch deletion of expired entries

All database operations use Result[T, E] for error handling.
"""

import random
import sqlite3
from datetime import datetime, timezone
from typing import NewType

from pydantic import BaseModel
from returns.result import Failure, Result, Success

from tasca.core.services.dedup_cleanup_service import (
    DEFAULT_CLEANUP_BATCH_SIZE,
    DEFAULT_DEDUP_TTL_SECONDS,
    DEFAULT_OPPORTUNISTIC_CLEANUP_PROBABILITY,
    calculate_dedup_cutoff_time,
    format_cutoff_for_sql,
    is_dedup_entry_expired,
    should_cleanup_opportunistically,
)
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
    conn: sqlite3.Connection,
    content_hash: str,
    content_preview: str,
    now: datetime | None = None,
) -> Result[DedupRecord, DedupError]:
    """Store a new dedup record.

    Args:
        conn: Database connection.
        content_hash: SHA-256 hash of the content.
        content_preview: Truncated preview for display.
        now: Current timestamp (defaults to UTC now, for testing).

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
    if now is None:
        now = datetime.now(timezone.utc)
    now_str = now.isoformat()

    try:
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


# =============================================================================
# Expiry-Aware Operations
# =============================================================================


# @shell_complexity: Expiry check + conditional delete (4 branches is acceptable for DB orchestration)
# @shell_orchestration: Expiry-aware duplicate check with opportunistic cleanup
def check_duplicate_with_expiry(
    conn: sqlite3.Connection,
    content_hash: str,
    ttl_seconds: int = DEFAULT_DEDUP_TTL_SECONDS,
    now: datetime | None = None,
) -> Result[DedupRecord | None, DedupError]:
    """Check if content hash exists and is not expired.

    Expired entries are treated as misses (not found). If an expired entry
    is found, it is deleted and None is returned.

    Args:
        conn: Database connection.
        content_hash: SHA-256 hash of the content.
        ttl_seconds: Time-to-live in seconds (default 24 hours).
        now: Current timestamp (defaults to UTC now).

    Returns:
        Success with DedupRecord if found and not expired,
        Success(None) if not found or expired,
        or Failure with error.

    Example:
        >>> from tasca.shell.storage.database import apply_schema
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> result = check_duplicate_with_expiry(conn, "nonexistent")
        >>> isinstance(result, Success) and result.unwrap() is None
        True
        >>> conn.close()
    """
    if now is None:
        now = datetime.now(timezone.utc)

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

        # Check if expired
        if is_dedup_entry_expired(record.first_seen_at, ttl_seconds, now):
            # Delete expired entry and return None (miss)
            conn.execute(
                "DELETE FROM dedup WHERE content_hash = ?",
                (content_hash,),
            )
            conn.commit()
            return Success(None)

        return Success(record)

    except sqlite3.Error as e:
        return Failure(DedupError(f"Database error: {e}"))


# @shell_orchestration: Batch cleanup of expired entries
def cleanup_expired_dedup_entries(
    conn: sqlite3.Connection,
    ttl_seconds: int = DEFAULT_DEDUP_TTL_SECONDS,
    now: datetime | None = None,
    batch_size: int = DEFAULT_CLEANUP_BATCH_SIZE,
) -> Result[int, DedupError]:
    """Delete expired entries from the dedup table.

    Periodic cleanup operation that removes entries older than TTL.
    Uses batching to limit the number of rows deleted in a single transaction.

    Args:
        conn: Database connection.
        ttl_seconds: Time-to-live in seconds (default 24 hours).
        now: Current timestamp (defaults to UTC now).
        batch_size: Maximum entries to delete in one call (default 100).

    Returns:
        Success with count of deleted entries, or Failure with error.

    Example:
        >>> from tasca.shell.storage.database import apply_schema
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> result = cleanup_expired_dedup_entries(conn)
        >>> isinstance(result, Success)
        True
        >>> result.unwrap()  # No expired entries
        0
        >>> conn.close()
    """
    if now is None:
        now = datetime.now(timezone.utc)

    cutoff = calculate_dedup_cutoff_time(now, ttl_seconds)
    cutoff_str = format_cutoff_for_sql(cutoff)

    try:
        # Use batched delete with LIMIT
        # SQLite doesn't support DELETE with LIMIT directly in all versions
        # So we use a subquery approach
        cursor = conn.execute(
            """
            DELETE FROM dedup
            WHERE rowid IN (
                SELECT rowid FROM dedup
                WHERE first_seen_at < ?
                LIMIT ?
            )
            """,
            (cutoff_str, batch_size),
        )
        deleted_count = cursor.rowcount
        conn.commit()

        return Success(deleted_count)

    except sqlite3.Error as e:
        conn.rollback()
        return Failure(DedupError(f"Cleanup failed: {e}"))


# @shell_orchestration: Opportunistic cleanup trigger
def opportunistic_cleanup(
    conn: sqlite3.Connection,
    ttl_seconds: int = DEFAULT_DEDUP_TTL_SECONDS,
    now: datetime | None = None,
    batch_size: int = DEFAULT_CLEANUP_BATCH_SIZE,
    cleanup_probability: float = DEFAULT_OPPORTUNISTIC_CLEANUP_PROBABILITY,
) -> Result[int, DedupError]:
    """Trigger opportunistic cleanup based on probability.

    Called during regular operations (e.g., store_or_get_existing) to
    perform periodic cleanup without explicit scheduling.

    Args:
        conn: Database connection.
        ttl_seconds: Time-to-live in seconds (default 24 hours).
        now: Current timestamp (defaults to UTC now).
        batch_size: Maximum entries to delete (default 100).
        cleanup_probability: Probability of triggering (default 0.1 = 10%).

    Returns:
        Success with count of deleted entries (may be 0 if not triggered),
        or Failure with error.

    Example:
        >>> from tasca.shell.storage.database import apply_schema
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> # With probability 0, never triggers
        >>> result = opportunistic_cleanup(conn, cleanup_probability=0.0)
        >>> isinstance(result, Success)
        True
        >>> result.unwrap()  # 0 because not triggered
        0
        >>> # With probability 1, always triggers
        >>> result = opportunistic_cleanup(conn, cleanup_probability=1.0)
        >>> isinstance(result, Success)
        True
        >>> conn.close()
    """
    # Inject random value from shell layer (I/O)
    random_value = random.random()
    if not should_cleanup_opportunistically(cleanup_probability, random_value):
        return Success(0)

    return cleanup_expired_dedup_entries(conn, ttl_seconds, now, batch_size)


# =============================================================================
# Updated store_or_get_existing with Expiry Support
# =============================================================================


# @shell_complexity: Expiry check + cleanup + store logic (5 branches for multi-step DB orchestration)
# @shell_orchestration: Combined operation with return_existing semantics and expiry
def store_or_get_existing_with_expiry(
    conn: sqlite3.Connection,
    content: str,
    ttl_seconds: int = DEFAULT_DEDUP_TTL_SECONDS,
    now: datetime | None = None,
    enable_opportunistic_cleanup: bool = True,
) -> Result[tuple[DedupRecord, bool], DedupError]:
    """Store content hash or return existing record with expiry support.

    Same semantics as store_or_get_existing, but:
    - Expired entries are treated as misses (deleted, new record created)
    - Optionally triggers opportunistic cleanup

    Args:
        conn: Database connection.
        content: Content string to deduplicate.
        ttl_seconds: Time-to-live in seconds (default 24 hours).
        now: Current timestamp (defaults to UTC now).
        enable_opportunistic_cleanup: Whether to trigger cleanup (default True).

    Returns:
        Success with (DedupRecord, is_new) tuple:
        - is_new=True: New record was created
        - is_new=False: Existing valid record was returned
        Or Failure with error.

    Example:
        >>> from tasca.shell.storage.database import apply_schema
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> # First call - creates new record
        >>> result1 = store_or_get_existing_with_expiry(conn, "Hello, world!")
        >>> isinstance(result1, Success)
        True
        >>> record1, is_new1 = result1.unwrap()
        >>> is_new1
        True
        >>> # Second call - returns existing
        >>> result2 = store_or_get_existing_with_expiry(conn, "Hello, world!")
        >>> record2, is_new2 = result2.unwrap()
        >>> is_new2
        False
        >>> conn.close()
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Opportunistic cleanup (ignore errors - not critical)
    if enable_opportunistic_cleanup:
        opport_result = opportunistic_cleanup(conn, ttl_seconds, now)
        # Log error in production, but don't fail the operation
        _ = opport_result  # Suppress unused warning

    # Compute hash and preview (pure function from core)
    content_hash, content_preview = compute_hash_and_preview(content)

    # Check for existing record with expiry check
    existing_result = check_duplicate_with_expiry(conn, content_hash, ttl_seconds, now)
    if isinstance(existing_result, Failure):
        return existing_result

    existing = existing_result.unwrap()
    if existing is not None:
        # Return existing valid record with is_new=False
        return Success((existing, False))

    # Store new record (with controlled timestamp)
    store_result = store_dedup(conn, content_hash, content_preview, now)
    if isinstance(store_result, Failure):
        return store_result

    # Return new record with is_new=True
    return Success((store_result.unwrap(), True))


# =============================================================================
# Private Helpers
# =============================================================================


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
