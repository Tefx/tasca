"""
Limited saying operations - Shell layer with limits enforcement.

This module provides I/O operations that enforce server-side limits
before performing actions. It combines core limits validation with
repository operations.

All functions return Result[T, E] for error handling.
"""

import sqlite3
from typing import NewType

from returns.result import Failure, Result, Success

from tasca.core.domain.saying import Saying, Speaker
from tasca.core.services.limits_service import (
    LimitError,
    LimitsConfig,
    check_content_limits,
)
from tasca.shell.storage.saying_repo import (
    append_saying,
    count_sayings_by_table,
    get_table_content_bytes,
)

# Error types
LimitedSayingError = NewType("LimitedSayingError", str)


# @shell_complexity: 4 branches - 2 for count/bytes fetch + 2 for limit check flow
def append_saying_with_limits(
    conn: sqlite3.Connection,
    table_id: str,
    speaker: Speaker,
    content: str,
    limits: LimitsConfig,
) -> Result[Saying, LimitError | LimitedSayingError]:
    """Append a saying with limits enforcement.

    This checks all configured limits before appending:
    1. Content length limit
    2. History count limit
    3. Bytes limit
    4. Mentions limit

    If any limit is exceeded, returns Failure with LimitError.
    If all limits pass, atomically appends the saying.

    Args:
        conn: Database connection (must have transaction support).
        table_id: UUID of the table.
        speaker: Speaker information.
        content: Markdown content of the saying.
        limits: Limits configuration.

    Returns:
        Success with the created Saying, or Failure with LimitError or SayingError.

    Example:
        >>> from tasca.shell.storage.database import apply_schema
        >>> from tasca.core.services.limits_service import LimitsConfig
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> limits = LimitsConfig(max_content_length=100, max_sayings_per_table=10)
        >>> # Append would work if we had a proper setup
    """
    # Get current counts for limit checking
    count_result = count_sayings_by_table(conn, table_id)
    if isinstance(count_result, Failure):
        return Failure(LimitedSayingError(f"Failed to get saying count: {count_result.failure()}"))
    current_count = count_result.unwrap()

    bytes_result = get_table_content_bytes(conn, table_id)
    if isinstance(bytes_result, Failure):
        return Failure(LimitedSayingError(f"Failed to get content bytes: {bytes_result.failure()}"))
    current_bytes = bytes_result.unwrap()

    # Check all limits
    limit_error = check_content_limits(content, current_count, current_bytes, limits)
    if limit_error is not None:
        return Failure(limit_error)

    # All limits passed, perform the append
    result = append_saying(conn, table_id, speaker, content)
    if isinstance(result, Failure):
        return Failure(LimitedSayingError(result.failure()))

    return Success(result.unwrap())


def get_limits_status_for_table(
    conn: sqlite3.Connection,
    table_id: str,
    limits: LimitsConfig,
) -> Result[dict[str, dict[str, int | float]], LimitedSayingError]:
    """Get the current limits status for a table.

    Returns a dict with limit name -> {current, limit, remaining, percentage}.

    Args:
        conn: Database connection.
        table_id: UUID of the table.
        limits: Limits configuration.

    Returns:
        Success with limits status dict, or Failure with error.
    """
    from tasca.core.services.limits_service import get_limits_status

    count_result = count_sayings_by_table(conn, table_id)
    if isinstance(count_result, Failure):
        return Failure(LimitedSayingError(f"Failed to get saying count: {count_result.failure()}"))
    current_count = count_result.unwrap()

    bytes_result = get_table_content_bytes(conn, table_id)
    if isinstance(bytes_result, Failure):
        return Failure(LimitedSayingError(f"Failed to get content bytes: {bytes_result.failure()}"))
    current_bytes = bytes_result.unwrap()

    status = get_limits_status(current_count, current_bytes, limits)
    return Success(status)
