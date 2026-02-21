"""
Idempotency key repository - SQLite implementation for explicit dedup_id operations.

This module provides idempotent write operations based on explicit dedup_id parameters.
Key scope: {resource_key, tool_name, dedup_id}

Dedup_ttl_hours default: 24 hours (from spec).

All database operations use Result[T, E] for error handling.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, NewType

from pydantic import BaseModel
from returns.result import Failure, Result, Success

# Type for repository errors
IdempotencyError = NewType("IdempotencyError", str)

# Default TTL in seconds (24 hours, per spec)
DEFAULT_IDEMPOTENCY_TTL_SECONDS = 86400


class IdempotencyRecord(BaseModel):
    """An idempotency record for dedup_id-based operations.

    Attributes:
        resource_key: Scope identifier (e.g., table_id for tables, table_id for sayings).
        tool_name: Name of the MCP tool (e.g., "table_create", "table_say").
        dedup_id: Client-provided idempotency key.
        response_data: Cached response as JSON string.
        created_at: When the record was created.
        expires_at: When the record expires (for cleanup).
    """

    resource_key: str
    tool_name: str
    dedup_id: str
    response_data: str
    created_at: datetime
    expires_at: datetime


# @shell_complexity: DB lookup + expiry check + delete + JSON decode is justified for dedup semantics
# @shell_orchestration: Repository operation with Result type
def check_idempotency_key(
    conn: sqlite3.Connection,
    resource_key: str,
    tool_name: str,
    dedup_id: str,
    now: datetime | None = None,
) -> Result[dict[str, Any] | None, IdempotencyError]:
    """Check if an idempotency key exists and is not expired.

    Args:
        conn: Database connection.
        resource_key: Scope identifier (dedup scope partition).
        tool_name: Name of the MCP tool.
        dedup_id: Client-provided idempotency key.
        now: Current timestamp (defaults to UTC now).

    Returns:
        Success with parsed response dict if found and not expired,
        Success(None) if not found or expired,
        or Failure with error.

    Example:
        >>> from tasca.shell.storage.database import apply_schema
        >>> import sqlite3
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> result = check_idempotency_key(conn, "table-123", "table_say", "dedup-456")
        >>> isinstance(result, Success) and result.unwrap() is None
        True
        >>> conn.close()
    """
    if now is None:
        now = datetime.now(timezone.utc)

    try:
        cursor = conn.execute(
            """
            SELECT response_data, created_at, expires_at
            FROM idempotency_keys
            WHERE resource_key = ? AND tool_name = ? AND dedup_id = ?
            """,
            (resource_key, tool_name, dedup_id),
        )
        row = cursor.fetchone()

        if not row:
            return Success(None)

        response_data_str, created_at_str, expires_at_str = row
        expires_at = datetime.fromisoformat(expires_at_str)

        # Check if expired
        if now > expires_at:
            # Delete expired entry
            conn.execute(
                "DELETE FROM idempotency_keys WHERE resource_key = ? AND tool_name = ? AND dedup_id = ?",
                (resource_key, tool_name, dedup_id),
            )
            conn.commit()
            return Success(None)

        # Parse and return the cached response
        response_data = json.loads(response_data_str)
        return Success(response_data)

    except sqlite3.Error as e:
        return Failure(IdempotencyError(f"Database error: {e}"))
    except json.JSONDecodeError as e:
        return Failure(IdempotencyError(f"Invalid JSON in stored response: {e}"))


# @shell_orchestration: Store idempotency key with response
def store_idempotency_key(
    conn: sqlite3.Connection,
    resource_key: str,
    tool_name: str,
    dedup_id: str,
    response_data: dict[str, Any],
    ttl_seconds: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    now: datetime | None = None,
) -> Result[None, IdempotencyError]:
    """Store an idempotency key with cached response.

    Args:
        conn: Database connection.
        resource_key: Scope identifier (dedup scope partition).
        tool_name: Name of the MCP tool.
        dedup_id: Client-provided idempotency key.
        response_data: Response dict to cache.
        ttl_seconds: Time-to-live in seconds (default 24 hours).
        now: Current timestamp (defaults to UTC now).

    Returns:
        Success(None) on success, or Failure with error.

    Example:
        >>> from tasca.shell.storage.database import apply_schema
        >>> import sqlite3
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> result = store_idempotency_key(
        ...     conn, "table-123", "table_say", "dedup-456", {"status": "ok"}
        ... )
        >>> isinstance(result, Success)
        True
        >>> conn.close()
    """
    if now is None:
        now = datetime.now(timezone.utc)

    from datetime import timedelta

    expires_at = now + timedelta(seconds=ttl_seconds)

    response_data_str = json.dumps(response_data)

    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO idempotency_keys
            (resource_key, tool_name, dedup_id, response_data, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                resource_key,
                tool_name,
                dedup_id,
                response_data_str,
                now.isoformat(),
                expires_at.isoformat(),
            ),
        )
        conn.commit()
        return Success(None)

    except sqlite3.Error as e:
        return Failure(IdempotencyError(f"Database error: {e}"))


# @shell_orchestration: Cleanup expired idempotency keys
def cleanup_expired_idempotency_keys(
    conn: sqlite3.Connection,
    now: datetime | None = None,
    batch_size: int = 100,
) -> Result[int, IdempotencyError]:
    """Delete expired idempotency keys.

    Args:
        conn: Database connection.
        now: Current timestamp (defaults to UTC now).
        batch_size: Maximum keys to delete in one call.

    Returns:
        Success with count of deleted keys, or Failure with error.

    Example:
        >>> from tasca.shell.storage.database import apply_schema
        >>> import sqlite3
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> result = cleanup_expired_idempotency_keys(conn)
        >>> isinstance(result, Success)
        True
        >>> result.unwrap()  # No expired keys
        0
        >>> conn.close()
    """
    if now is None:
        now = datetime.now(timezone.utc)

    try:
        cursor = conn.execute(
            """
            DELETE FROM idempotency_keys
            WHERE rowid IN (
                SELECT rowid FROM idempotency_keys
                WHERE expires_at < ?
                LIMIT ?
            )
            """,
            (now.isoformat(), batch_size),
        )
        deleted_count = cursor.rowcount
        conn.commit()
        return Success(deleted_count)

    except sqlite3.Error as e:
        conn.rollback()
        return Failure(IdempotencyError(f"Cleanup failed: {e}"))
