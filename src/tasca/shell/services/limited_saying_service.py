"""
Limited saying operations - Shell layer with limits enforcement.

This module provides I/O operations that enforce server-side limits
before performing actions. It combines core limits validation with
repository operations.

All functions return Result[T, E] for error handling.
"""

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from typing import NewType

from returns.result import Failure, Result, Success

from tasca.core.domain.patron import PatronId
from tasca.core.domain.saying import Saying, Speaker, SpeakerKind
from tasca.core.domain.table import TableId
from tasca.core.services.limits_service import (
    LimitError,
    LimitsConfig,
    check_content_limits,
)
from tasca.core.table_state_machine import can_say
from tasca.shell.storage.patron_repo import PatronNotFoundError, get_patron
from tasca.shell.storage.saying_repo import (
    append_saying,
    count_sayings_by_table,
    get_table_content_bytes,
)
from tasca.shell.storage.table_repo import TableNotFoundError, get_table

# Error types
LimitedSayingError = NewType("LimitedSayingError", str)


class TableSayErrorKind(StrEnum):
    """Transport-neutral table_say/append-saying error classes."""

    TABLE_NOT_FOUND = "table_not_found"
    TABLE_LOOKUP_FAILED = "table_lookup_failed"
    OPERATION_NOT_ALLOWED = "operation_not_allowed"
    INVALID_SPEAKER = "invalid_speaker"
    PATRON_NOT_FOUND = "patron_not_found"
    PATRON_LOOKUP_FAILED = "patron_lookup_failed"
    LIMIT_EXCEEDED = "limit_exceeded"
    APPEND_FAILED = "append_failed"


@dataclass(frozen=True)
class TableSayOutcome:
    """Successful transport-neutral append-saying outcome."""

    saying: Saying
    speaker_key: str


@dataclass(frozen=True)
class TableSayError:
    """Typed transport-neutral append-saying failure."""

    kind: TableSayErrorKind
    message: str
    table_status: str | None = None
    speaker_kind: str | None = None
    patron_id: str | None = None
    limit_error: LimitError | None = None
    cause: str | None = None


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


# @invar:allow shell_result: Shared adapter pre-check returns optional typed error for idempotency ordering.
def validate_table_say_speaker_constraints(
    speaker_kind: str,
    patron_id: str | None,
) -> TableSayError | None:
    if speaker_kind == "agent" and patron_id is None:
        return TableSayError(
            kind=TableSayErrorKind.INVALID_SPEAKER,
            message="patron_id is required when speaker_kind is 'agent'",
            speaker_kind=speaker_kind,
        )
    if speaker_kind == "human" and patron_id is not None:
        return TableSayError(
            kind=TableSayErrorKind.INVALID_SPEAKER,
            message="patron_id must be null or omitted when speaker_kind is 'human'",
            speaker_kind=speaker_kind,
            patron_id=patron_id,
        )
    if speaker_kind not in {"agent", "human"}:
        return TableSayError(
            kind=TableSayErrorKind.INVALID_SPEAKER,
            message=f"speaker_kind must be 'agent' or 'human', got {speaker_kind!r}",
            speaker_kind=speaker_kind,
            patron_id=patron_id,
        )
    return None


# @shell_complexity: Transport-neutral speaker resolution includes optional patron lookup/error classification.
def _resolve_speaker(
    conn: sqlite3.Connection,
    speaker_kind: str,
    patron_id: str | None,
    speaker_name: str | None,
) -> Result[tuple[Speaker, str], TableSayError]:
    resolved_name = speaker_name
    if resolved_name is None:
        if patron_id is not None:
            patron_result = get_patron(conn, PatronId(patron_id))
            if isinstance(patron_result, Failure):
                error = patron_result.failure()
                if isinstance(error, PatronNotFoundError):
                    return Failure(
                        TableSayError(
                            kind=TableSayErrorKind.PATRON_NOT_FOUND,
                            message=f"Patron not found: {patron_id}",
                            patron_id=patron_id,
                        )
                    )
                return Failure(
                    TableSayError(
                        kind=TableSayErrorKind.PATRON_LOOKUP_FAILED,
                        message=f"Failed to get patron: {error}",
                        patron_id=patron_id,
                        cause=str(error),
                    )
                )
            resolved_name = patron_result.unwrap().name
        else:
            resolved_name = "Human"

    if speaker_kind == "agent":
        assert patron_id is not None
        return Success(
            (
                Speaker(kind=SpeakerKind.AGENT, name=resolved_name, patron_id=PatronId(patron_id)),
                patron_id,
            )
        )
    return Success((Speaker(kind=SpeakerKind.HUMAN, name=resolved_name, patron_id=None), "human"))


# @invar:allow shell_complexity: Shared transport-neutral table_say operation owns lookup/state/speaker/limit/append orchestration.
def append_saying_operation(
    conn: sqlite3.Connection,
    table_id: str,
    content: str,
    speaker_kind: str,
    patron_id: str | None,
    speaker_name: str | None,
    limits: LimitsConfig,
) -> Result[TableSayOutcome, TableSayError]:
    """Append a saying through the shared table_say business operation.

    The operation owns table lookup, table state validation, transport-neutral
    speaker constraints/resolution, limit enforcement, append, and typed error
    classification. HTTP/MCP adapters map these outcomes to local envelopes.
    """
    validation_error = validate_table_say_speaker_constraints(speaker_kind, patron_id)
    if validation_error is not None:
        return Failure(validation_error)

    table_result = get_table(conn, TableId(table_id))
    if isinstance(table_result, Failure):
        error = table_result.failure()
        if isinstance(error, TableNotFoundError):
            return Failure(
                TableSayError(
                    kind=TableSayErrorKind.TABLE_NOT_FOUND,
                    message=f"Table not found: {table_id}",
                    cause=str(error),
                )
            )
        return Failure(
            TableSayError(
                kind=TableSayErrorKind.TABLE_LOOKUP_FAILED,
                message=f"Failed to get table: {error}",
                cause=str(error),
            )
        )

    table = table_result.unwrap()
    if not can_say(table.status):
        return Failure(
            TableSayError(
                kind=TableSayErrorKind.OPERATION_NOT_ALLOWED,
                message=(
                    f"Cannot add saying to table with status '{table.status.value}'. "
                    "Table must be OPEN or PAUSED."
                ),
                table_status=table.status.value,
            )
        )

    speaker_result = _resolve_speaker(conn, speaker_kind, patron_id, speaker_name)
    if isinstance(speaker_result, Failure):
        return Failure(speaker_result.failure())
    speaker, speaker_key = speaker_result.unwrap()

    append_result = append_saying_with_limits(conn, table_id, speaker, content, limits)
    if isinstance(append_result, Failure):
        error = append_result.failure()
        if isinstance(error, LimitError):
            return Failure(
                TableSayError(
                    kind=TableSayErrorKind.LIMIT_EXCEEDED,
                    message=error.message,
                    limit_error=error,
                )
            )
        return Failure(
            TableSayError(
                kind=TableSayErrorKind.APPEND_FAILED,
                message=f"Failed to append saying: {error}",
                cause=str(error),
            )
        )

    return Success(TableSayOutcome(saying=append_result.unwrap(), speaker_key=speaker_key))


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
