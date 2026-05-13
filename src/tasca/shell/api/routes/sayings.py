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

from pydantic import BaseModel, Field
from returns.result import Failure, Result, Success

from tasca.config import settings
from tasca.core.domain.saying import Saying
from tasca.core.domain.table import Table, TableId
from tasca.core.services.limits_service import (
    LimitsConfig,
    settings_to_limits_config,
)
from tasca.shell.api.auth import verify_admin_token
from tasca.shell.api.deps import get_db
from tasca.shell.api.errors import raise_http_error
from tasca.shell.api.fastapi_compat import APIRouter, Depends, HTTPException, Query, status
from tasca.shell.logging import get_logger, log_say, log_wait_returned, log_wait_timeout
from tasca.shell.services.limited_saying_service import (
    TableSayError,
    TableSayErrorKind,
    append_saying_operation,
)
from tasca.shell.storage.saying_repo import (
    get_table_max_sequence,
    list_sayings_by_table,
)
from tasca.shell.storage.table_repo import TableNotFoundError, get_table

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


# @shell_orchestration: Converts global settings to LimitsConfig for use in routes
def _get_limits_config() -> Result[LimitsConfig, str]:
    """Get limits configuration from application settings.

    Returns:
        LimitsConfig with values from settings.
    """
    config = settings_to_limits_config(settings)
    if config.max_content_length is None:
        return Success(LimitsConfig(
            max_sayings_per_table=config.max_sayings_per_table,
            max_content_length=65536,
            max_bytes_per_table=config.max_bytes_per_table,
            max_mentions_per_saying=config.max_mentions_per_saying,
        ))
    return Success(config)


# @shell_orchestration: Database lookup + HTTP error mapping
def _get_table_or_404(conn: sqlite3.Connection, table_id: str) -> Result[Table, HTTPException]:
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
            return Failure(HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table not found: {table_id}",
            ))
        return Failure(HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get table: {error}",
        ))

    return Success(result.unwrap())


def _append_saying_response(
    conn: sqlite3.Connection,
    table_id: str,
    data: SayingCreate,
) -> Result[Saying, HTTPException]:
    """Append a saying and return either the created saying or HTTP failure."""
    limits_result = _get_limits_config()
    if isinstance(limits_result, Failure):
        return Failure(HTTPException(status_code=500, detail=limits_result.failure()))
    speaker_kind = "agent" if data.patron_id is not None else "human"
    result = append_saying_operation(
        conn,
        table_id=table_id,
        content=data.content,
        speaker_kind=speaker_kind,
        patron_id=data.patron_id,
        speaker_name=data.speaker_name,
        limits=limits_result.unwrap(),
    )
    if isinstance(result, Failure):
        try:
            _raise_table_say_failure(result.failure())
        except HTTPException as exc:
            return Failure(exc)
    saying = result.unwrap().saying
    log_say(
        logger,
        table_id=saying.table_id,
        sequence=saying.sequence,
        speaker_kind=saying.speaker.kind.value,
        speaker_name=saying.speaker.name,
        patron_id=saying.speaker.patron_id,
    )
    return Success(saying)


def _list_sayings_response(
    conn: sqlite3.Connection,
    table_id: str,
    since_sequence: int,
    limit: int,
) -> Result[SayingListResponse, HTTPException]:
    """List sayings and calculate last-seen next_sequence semantics."""
    table_result = _get_table_or_404(conn, table_id)
    if isinstance(table_result, Failure):
        return Failure(table_result.failure())
    max_seq_result = get_table_max_sequence(conn, table_id)
    if isinstance(max_seq_result, Failure):
        return Failure(HTTPException(status_code=500, detail=f"Failed to get table max sequence: {max_seq_result.failure()}"))
    result = list_sayings_by_table(conn, table_id, since_sequence, limit)
    if isinstance(result, Failure):
        return Failure(HTTPException(status_code=500, detail=f"Failed to list sayings: {result.failure()}"))
    sayings = result.unwrap()
    next_sequence = max(s.sequence for s in sayings) if sayings else max_seq_result.unwrap()
    return Success(SayingListResponse(sayings=sayings, next_sequence=next_sequence))


def _wait_timeout_response(
    conn: sqlite3.Connection,
    table_id: str,
    since_sequence: int,
) -> Result[WaitResponse, HTTPException]:
    """Build the empty timeout response for a wait request."""
    max_seq_result = get_table_max_sequence(conn, table_id)
    if isinstance(max_seq_result, Failure):
        return Failure(HTTPException(status_code=500, detail=f"Failed to get table max sequence: {max_seq_result.failure()}"))
    log_wait_timeout(logger, table_id, since_sequence)
    return Success(WaitResponse(sayings=[], next_sequence=max_seq_result.unwrap(), timeout=True))


def _new_sayings_response(
    conn: sqlite3.Connection,
    table_id: str,
    since_sequence: int,
) -> Result[WaitResponse, HTTPException]:
    """Return all currently available sayings newer than ``since_sequence``."""
    full_result = list_sayings_by_table(conn, table_id, since_sequence, limit=100)
    if isinstance(full_result, Failure):
        return Failure(HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list sayings: {full_result.failure()}",
        ))
    full_sayings = full_result.unwrap()
    log_wait_returned(logger, table_id, since_sequence, len(full_sayings))
    return Success(WaitResponse(
        sayings=full_sayings,
        next_sequence=max(s.sequence for s in full_sayings),
        timeout=False,
    ))


# @shell_complexity: Long-poll orchestration combines table existence, bounded polling, sleep, and timeout response semantics.
async def _wait_for_sayings_response(
    conn: sqlite3.Connection,
    table_id: str,
    since_sequence: int,
    timeout: float,
) -> Result[WaitResponse, HTTPException]:
    """Poll for sayings newer than ``since_sequence`` until data is available or timeout expires."""
    table_result = _get_table_or_404(conn, table_id)
    if isinstance(table_result, Failure):
        return Failure(table_result.failure())

    end_time = time.monotonic() + timeout
    while time.monotonic() < end_time:
        result = list_sayings_by_table(conn, table_id, since_sequence, limit=1)
        if isinstance(result, Failure):
            return Failure(HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to check for sayings: {result.failure()}",
            ))
        if result.unwrap():
            return _new_sayings_response(conn, table_id, since_sequence)
        remaining = end_time - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(POLL_INTERVAL, remaining))

    return _wait_timeout_response(conn, table_id, since_sequence)


# @shell_orchestration: Transport-local HTTP status/detail mapping only.
# @shell_complexity: Maps each shared table_say failure family to public REST status/code.
def _raise_table_say_failure(error: TableSayError) -> None:
    """Raise the legacy HTTP response for a shared table_say failure."""
    if error.kind == TableSayErrorKind.LIMIT_EXCEEDED and error.limit_error is not None:
        raise_http_error(
            status.HTTP_400_BAD_REQUEST,
            "LimitExceeded",
            error.limit_error.message,
            LimitErrorResponse(
                error="limit_exceeded",
                limit_kind=error.limit_error.kind.value,
                limit=error.limit_error.limit,
                actual=error.limit_error.actual,
                message=error.limit_error.message,
            ).model_dump(),
        )

    if error.kind == TableSayErrorKind.TABLE_NOT_FOUND:
        raise_http_error(status.HTTP_404_NOT_FOUND, "TableNotFound", error.message)

    if error.kind == TableSayErrorKind.OPERATION_NOT_ALLOWED:
        raise_http_error(status.HTTP_403_FORBIDDEN, "PermissionDenied", error.message, {"table_status": error.table_status} if error.table_status else {})

    if error.kind == TableSayErrorKind.INVALID_SPEAKER:
        raise_http_error(status.HTTP_400_BAD_REQUEST, "InvalidRequest", error.message)

    raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "StorageError", error.message)


# =============================================================================
# POST /tables/{table_id}/sayings - Append a new saying
# =============================================================================


@router.post("", response_model=Saying, status_code=status.HTTP_201_CREATED)
async def append_saying_endpoint(
    table_id: str,
    data: SayingCreate,
    _auth: None = Depends(verify_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
) -> Saying:
    """Append a new saying to a table."""
    result = _append_saying_response(conn, table_id, data)
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()


# =============================================================================
# GET /tables/{table_id}/sayings - List sayings
# =============================================================================


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
    """List sayings for a table."""
    result = _list_sayings_response(conn, table_id, since_sequence, limit)
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()


# =============================================================================
# GET /tables/{table_id}/sayings/wait - Long-poll wait for new sayings
# =============================================================================

# Default timeout for wait endpoint (seconds)
DEFAULT_WAIT_TIMEOUT = 30.0
# Poll interval for checking new sayings (seconds)
POLL_INTERVAL = 0.5


@router.get("/wait", response_model=WaitResponse)
async def wait_for_sayings_endpoint(
    table_id: str,
    since_sequence: int = Query(..., ge=-1, description="Wait for sayings with sequence > this value"),
    timeout: float = Query(default=DEFAULT_WAIT_TIMEOUT, ge=0.0, le=120.0, description="Max wait time in seconds (0-120, default 30)"),
    conn: sqlite3.Connection = Depends(get_db),
) -> WaitResponse:
    """Long-poll wait for new sayings."""
    result = await _wait_for_sayings_response(conn, table_id, since_sequence, timeout)
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()
