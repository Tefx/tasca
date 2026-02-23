"""
Tables control API routes.

Endpoint for table lifecycle control operations (pause, resume, close).
Extracted from tables.py (SF-2) to keep that module below 500 lines.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from returns.result import Failure

from tasca.core.domain.saying import Speaker, SpeakerKind
from tasca.core.domain.table import TableId, TableUpdate, Version
from tasca.core.table_state_machine import (
    can_transition_to_closed,
    can_transition_to_open,
    can_transition_to_paused,
    transition_to_closed,
    transition_to_open,
    transition_to_paused,
)
from tasca.shell.api.auth import verify_admin_token
from tasca.shell.api.deps import get_db
from tasca.shell.storage.saying_repo import append_saying
from tasca.shell.storage.table_repo import (
    TableNotFoundError,
    VersionConflictError,
    get_table,
    update_table,
)

router = APIRouter()


# =============================================================================
# Request / Response Models
# =============================================================================


class TableControlRequest(BaseModel):
    """Request model for table control operations."""

    action: str = Field(..., description="Control action: pause, resume, or close")
    speaker_name: str = Field(..., description="Name of the speaker performing the action")
    reason: str | None = Field(None, description="Optional reason for the action")


class TableControlResponse(BaseModel):
    """Response model for table control operations."""

    table_status: str
    control_saying_sequence: int


# =============================================================================
# POST /tables/{table_id}/control - Control table lifecycle (Admin required)
# =============================================================================


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
# @invar:allow shell_result: HTTP route returns response model, not Result
# @invar:allow function_size: Control endpoint requires multi-step validation, saying append, and status update
@router.post("/{table_id}/control", response_model=TableControlResponse)
async def control_table_endpoint(
    table_id: str,
    data: TableControlRequest,
    _auth: None = Depends(verify_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
) -> TableControlResponse:
    """Control table lifecycle: pause, resume, or close.

    Requires admin authentication via Bearer token.

    State transitions:
    - pause: OPEN -> PAUSED
    - resume: PAUSED -> OPEN
    - close: OPEN|PAUSED -> CLOSED (terminal)

    A CONTROL saying is appended for audit trail before status update.

    Args:
        table_id: The table identifier.
        data: Control action (pause/resume/close), speaker name, and optional reason.
        _auth: Admin authentication (injected via dependency).
        conn: Database connection (injected via dependency).

    Returns:
        New table status and sequence number of the CONTROL saying.

    Raises:
        HTTPException: 400 if action is invalid.
        HTTPException: 404 if table not found.
        HTTPException: 409 if state transition is invalid.
        HTTPException: 500 if database operation fails.
    """
    # Validate action
    if data.action not in ("pause", "resume", "close"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid action: {data.action}. Must be 'pause', 'resume', or 'close'.",
        )

    # Fetch current table
    current_result = get_table(conn, TableId(table_id))

    if isinstance(current_result, Failure):
        error = current_result.failure()
        if isinstance(error, TableNotFoundError):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table not found: {table_id}",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get table: {error}",
        )

    current_table = current_result.unwrap()

    # Validate state transition using state machine
    if data.action == "pause":
        if not can_transition_to_paused(current_table.status):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot pause table in {current_table.status.value} state",
            )
        new_status = transition_to_paused(current_table.status)
    elif data.action == "resume":
        if not can_transition_to_open(current_table.status):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot resume table in {current_table.status.value} state",
            )
        new_status = transition_to_open(current_table.status)
    else:  # action == "close"
        if not can_transition_to_closed(current_table.status):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot close table in {current_table.status.value} state",
            )
        new_status = transition_to_closed(current_table.status)

    # Build CONTROL saying content
    control_content = f"**CONTROL: {data.action.upper()}**"
    if data.reason:
        control_content += f"\n\n{data.reason}"

    # Create speaker for CONTROL saying (human speaker, no patron_id)
    speaker = Speaker(kind=SpeakerKind.HUMAN, name=data.speaker_name)

    # Append CONTROL saying
    saying_result = append_saying(
        conn=conn,
        table_id=table_id,
        speaker=speaker,
        content=control_content,
    )

    if isinstance(saying_result, Failure):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to append control saying: {saying_result.failure()}",
        )

    saying = saying_result.unwrap()

    # Update table status via TableUpdate
    now = datetime.now(UTC)
    update = TableUpdate(
        question=current_table.question,
        context=current_table.context,
        status=new_status,
    )

    update_result = update_table(
        conn=conn,
        table_id=TableId(table_id),
        update=update,
        expected_version=current_table.version,
        now=now,
    )

    if isinstance(update_result, Failure):
        error = update_result.failure()
        if isinstance(error, VersionConflictError):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error.to_json(),
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update table status: {error}",
        )

    return TableControlResponse(
        table_status=new_status.value,
        control_saying_sequence=saying.sequence,
    )
