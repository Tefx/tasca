"""
Sayings API routes.

Endpoints for saying operations within tables:
- POST /tables/{table_id}/sayings - Append a new saying
- GET /tables/{table_id}/sayings - List sayings with next_sequence
- GET /tables/{table_id}/sayings/wait - Long-poll wait for new sayings
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from returns.result import Failure, Success

from tasca.config import settings
from tasca.core.domain.patron import PatronId
from tasca.core.domain.saying import Saying, Speaker, SpeakerKind
from tasca.core.domain.table import Table, TableId
from tasca.core.services.limits_service import (
    LimitsConfig,
    check_content_limits,
    settings_to_limits_config,
)
from tasca.core.table_state_machine import can_say
from tasca.shell.api.auth import verify_admin_token
from tasca.shell.api.deps import get_db
from tasca.shell.storage.saying_repo import (
    SayingError,
    append_saying,
    count_sayings_by_table,
    get_table_max_sequence,
    get_table_content_bytes,
    list_sayings_by_table,
)
from tasca.shell.storage.table_repo import TableNotFoundError, get_table
from tasca.shell.logging import get_logger, log_say, log_wait_timeout, log_wait_returned

if TYPE_CHECKING:
    from collections.abc import Generator

router = APIRouter()
logger = get_logger(__name__)


# =============================================================================
# Request/Response Models
# =============================================================================


class SayingCreate(BaseModel):
    """Request model for creating a new saying.

    Attributes:
        speaker_name: Display name of the speaker.
        content: Markdown content of the saying.
        patron_id: Optional patron ID. If None, speaker is human.
    """

    speaker_name: str = Field(..., description="Display name of the speaker", min_length=1)
    content: str = Field(..., description="Markdown content of the saying", min_length=1)
    patron_id: str | None = Field(None, description="Patron ID if speaker is an AI agent")


class SayingListResponse(BaseModel):
    """Response model for listing sayings.

    Includes next_sequence for clients to wait for new sayings.
    Clients should call /wait?since_sequence={next_sequence} to wait.

    next_sequence rules:
    - If sayings exist: next_sequence = max(sequence)  [last seen sequence]
    - If no sayings: next_sequence = -1
    Clients pass next_sequence back as since_sequence; server returns sequence > since_sequence.

    Attributes:
        sayings: List of sayings ordered by sequence (ascending).
        next_sequence: Last seen sequence (pass as since_sequence on next call).
    """

    sayings: list[Saying]
    next_sequence: int = Field(..., description="Sequence for next saying (use for wait)")


class WaitResponse(BaseModel):
    """Response model for wait endpoint.

    Attributes:
        sayings: List of new sayings (may be empty on timeout).
        next_sequence: Updated next_sequence value.
        timeout: True if the wait timed out without new sayings.
    """

    sayings: list[Saying]
    next_sequence: int
    timeout: bool = Field(..., description="True if wait timed out")


class LimitErrorResponse(BaseModel):
    """Response model for limit exceeded errors."""

    error: str = Field(..., description="Error type")
    limit_kind: str = Field(..., description="Type of limit exceeded")
    limit: int = Field(..., description="Configured limit value")
    actual: int = Field(..., description="Actual value that exceeded the limit")
    message: str = Field(..., description="Human-readable error message")


# =============================================================================
# Helper Functions
# =============================================================================


# @invar:allow shell_result: Thin adapter - retrieves config from settings (env vars)
# @shell_orchestration: Converts global settings to LimitsConfig for use in routes
def _get_limits_config() -> LimitsConfig:
    """Get limits configuration from application settings.

    Returns:
        LimitsConfig with values from settings.
    """
    return settings_to_limits_config(settings)


# @invar:allow shell_result: Helper raises HTTPException directly (no Result needed)
# @shell_orchestration: Database lookup + HTTP error mapping
def _get_table_or_404(conn: sqlite3.Connection, table_id: str) -> Table:
    """Get a table by ID or raise 404.

    Args:
        conn: Database connection.
        table_id: Table ID to retrieve.

    Returns:
        The Table if found.

    Raises:
        HTTPException: 404 if table not found, 500 on database error.
    """
    result = get_table(conn, TableId(table_id))

    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, TableNotFoundError):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table not found: {table_id}",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get table: {error}",
        )

    return result.unwrap()


# @invar:allow shell_result: Helper raises HTTPException directly (no Result needed)
# @shell_orchestration: State machine check + HTTP error mapping
def _validate_can_say(table: Table) -> None:
    """Validate that sayings can be added to the table.

    Args:
        table: The table to validate.

    Raises:
        HTTPException: 403 if table does not allow sayings.
    """
    if not can_say(table.status):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cannot add saying to table with status '{table.status.value}'. "
            f"Table must be OPEN or PAUSED.",
        )


# @invar:allow shell_result: Helper raises HTTPException directly (no Result needed)
# @shell_orchestration: Multiple database calls + limit checks + HTTP error mapping
def _check_limits_before_append(
    conn: sqlite3.Connection,
    table_id: str,
    content: str,
    config: LimitsConfig,
) -> None:
    """Check content limits before appending a saying.

    Args:
        conn: Database connection.
        table_id: Table ID.
        content: Content to validate.
        config: Limits configuration.

    Raises:
        HTTPException: 400 if limit exceeded, 500 on database error.
    """
    # Get current counts
    count_result = count_sayings_by_table(conn, table_id)
    if isinstance(count_result, Failure):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to count sayings: {count_result.failure()}",
        )
    current_count = count_result.unwrap()

    # Get current bytes
    bytes_result = get_table_content_bytes(conn, table_id)
    if isinstance(bytes_result, Failure):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get table bytes: {bytes_result.failure()}",
        )
    current_bytes = bytes_result.unwrap()

    # Check all limits
    limit_error = check_content_limits(content, current_count, current_bytes, config)

    if limit_error is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=LimitErrorResponse(
                error="limit_exceeded",
                limit_kind=limit_error.kind.value,
                limit=limit_error.limit,
                actual=limit_error.actual,
                message=limit_error.message,
            ).model_dump(),
        )


# =============================================================================
# POST /tables/{table_id}/sayings - Append a new saying
# =============================================================================


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
@router.post("", response_model=Saying, status_code=status.HTTP_201_CREATED)
async def append_saying_endpoint(
    table_id: str,
    data: SayingCreate,
    _auth: None = Depends(verify_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
) -> Saying:
    """Append a new saying to a table.

    Requires admin authentication via Bearer token.

    Creates a new saying with automatically allocated sequence number.
    Enforces server-side limits (content length, history count, bytes, mentions).
    Enforces state machine guard: only OPEN or PAUSED tables can have sayings added.

    Args:
        table_id: The table identifier.
        data: Saying creation data (speaker_name, content, optional patron_id).
        _auth: Admin authentication (injected via dependency).
        conn: Database connection (injected via dependency).

    Returns:
        The created saying with assigned id and sequence.

    Raises:
        HTTPException: 401 if missing or invalid admin token.
        HTTPException: 404 if table not found.
        HTTPException: 403 if table state doesn't allow sayings.
        HTTPException: 400 if limits exceeded.
        HTTPException: 500 if database operation fails.
    """
    # Get table and validate state
    table = _get_table_or_404(conn, table_id)
    _validate_can_say(table)

    # Check limits
    limits_config = _get_limits_config()
    _check_limits_before_append(conn, table_id, data.content, limits_config)

    # Create speaker
    if data.patron_id is not None:
        speaker = Speaker(
            kind=SpeakerKind.AGENT,
            name=data.speaker_name,
            patron_id=PatronId(data.patron_id),
        )
    else:
        speaker = Speaker(
            kind=SpeakerKind.HUMAN,
            name=data.speaker_name,
            patron_id=None,
        )

    # Append saying
    result = append_saying(conn, table_id, speaker, data.content)

    if isinstance(result, Failure):
        error = result.failure()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to append saying: {error}",
        )

    saying = result.unwrap()
    
    # Log saying append
    log_say(
        logger,
        table_id=saying.table_id,
        sequence=saying.sequence,
        speaker_kind=saying.speaker.kind.value,
        speaker_name=saying.speaker.name,
        patron_id=saying.speaker.patron_id,
    )
    
    return saying


# =============================================================================
# GET /tables/{table_id}/sayings - List sayings
# =============================================================================


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
@router.get("", response_model=SayingListResponse)
async def list_sayings_endpoint(
    table_id: str,
    since_sequence: int = Query(
        default=-1,
        ge=-1,
        description="Get sayings with sequence > this value (-1 for all)",
    ),
    limit: int = Query(default=50, ge=1, le=200, description="Max sayings to return"),
    conn: sqlite3.Connection = Depends(get_db),
) -> SayingListResponse:
    """List sayings for a table.

    Returns sayings ordered by sequence (ascending), with next_sequence
    for the client to use when polling for new sayings.

    next_sequence rules (last-seen semantics):
    - If sayings returned: next_sequence = max(sequence in results)
    - If no sayings in table: next_sequence = -1
    - After filtering by since_sequence, if results empty but table has sayings:
      next_sequence = max(sequence in table)
    Pass next_sequence as since_sequence; server returns sequence > since_sequence.

    Args:
        table_id: The table identifier.
        since_sequence: Get sayings with sequence > this value (-1 for all).
        limit: Maximum number of sayings to return (1-200, default 50).
        conn: Database connection (injected via dependency).

    Returns:
        SayingListResponse with sayings and next_sequence.

    Raises:
        HTTPException: 404 if table not found.
        HTTPException: 500 if database operation fails.
    """
    # Validate table exists
    _get_table_or_404(conn, table_id)

    # Get max sequence for the table (for next_sequence calculation)
    max_seq_result = get_table_max_sequence(conn, table_id)
    if isinstance(max_seq_result, Failure):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get table max sequence: {max_seq_result.failure()}",
        )
    table_max_sequence = max_seq_result.unwrap()

    # List sayings
    result = list_sayings_by_table(conn, table_id, since_sequence, limit)

    if isinstance(result, Failure):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list sayings: {result.failure()}",
        )

    sayings = result.unwrap()

    # next_sequence = last sequence seen (NOT +1).
    # Clients pass it back as since_sequence; server returns sequence > since_sequence.
    # So next_sequence must equal the last seen sequence for the next call to
    # catch sequence (last+1). Using last+1 would require sequence > last+1,
    # which skips the very next message.
    if sayings:
        next_sequence = max(s.sequence for s in sayings)
    else:
        # table_max_sequence is -1 when truly empty → client polls since_sequence=-1
        # (sequence > -1 = all sayings, catches sequence=0).
        # Otherwise equals the max sequence already seen by the client.
        next_sequence = table_max_sequence

    return SayingListResponse(sayings=sayings, next_sequence=next_sequence)


# =============================================================================
# GET /tables/{table_id}/sayings/wait - Long-poll wait for new sayings
# =============================================================================

# Default timeout for wait endpoint (seconds)
DEFAULT_WAIT_TIMEOUT = 30.0
# Poll interval for checking new sayings (seconds)
POLL_INTERVAL = 0.5


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
@router.get("/wait", response_model=WaitResponse)
async def wait_for_sayings_endpoint(
    table_id: str,
    since_sequence: int = Query(
        ...,
        ge=-1,
        description="Wait for sayings with sequence > this value",
    ),
    timeout: float = Query(
        default=DEFAULT_WAIT_TIMEOUT,
        ge=0.0,
        le=120.0,
        description="Max wait time in seconds (0-120, default 30)",
    ),
    conn: sqlite3.Connection = Depends(get_db),
) -> WaitResponse:
    """Long-poll wait for new sayings.

    Blocks until a new saying (sequence > since_sequence) is available,
    or until timeout. Clients should use next_sequence from previous
    list/wait response as since_sequence.

    Args:
        table_id: The table identifier.
        since_sequence: Wait for sayings with sequence > this value.
        timeout: Max wait time in seconds (0-120, default 30).
        conn: Database connection (injected via dependency).

    Returns:
        WaitResponse with:
        - sayings: New sayings (empty if timed out)
        - next_sequence: Updated next_sequence value
        - timeout: True if wait timed out without new sayings

    Raises:
        HTTPException: 404 if table not found.
        HTTPException: 500 if database operation fails.

    Note:
        This implementation uses simple polling with POLL_INTERVAL.
        For higher scale, consider using SQLite NOTIFY/LISTEN or
        a message queue for real-time notifications.
    """
    # Validate table exists
    _get_table_or_404(conn, table_id)

    start_time = time.monotonic()
    end_time = start_time + timeout

    while time.monotonic() < end_time:
        # Check for new sayings
        result = list_sayings_by_table(conn, table_id, since_sequence, limit=1)

        if isinstance(result, Failure):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to check for sayings: {result.failure()}",
            )

        sayings = result.unwrap()

        if sayings:
            # Found new saying(s) - return them
            # Get full list (may be more than 1)
            full_result = list_sayings_by_table(conn, table_id, since_sequence, limit=100)
            if isinstance(full_result, Failure):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to list sayings: {full_result.failure()}",
                )
            full_sayings = full_result.unwrap()
            next_sequence = max(s.sequence for s in full_sayings)

            # Log wait returned
            log_wait_returned(logger, table_id, since_sequence, len(full_sayings))

            return WaitResponse(
                sayings=full_sayings,
                next_sequence=next_sequence,
                timeout=False,
            )

        # Wait before next poll
        remaining = end_time - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(POLL_INTERVAL, remaining))

    # Timeout - return empty with current next_sequence
    max_seq_result = get_table_max_sequence(conn, table_id)
    if isinstance(max_seq_result, Failure):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get table max sequence: {max_seq_result.failure()}",
        )
    table_max_sequence = max_seq_result.unwrap()

    # Log wait timeout
    log_wait_timeout(logger, table_id, since_sequence)

    return WaitResponse(
        sayings=[],
        next_sequence=table_max_sequence,
        timeout=True,
    )
