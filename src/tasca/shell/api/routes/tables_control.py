"""
Tables control API routes.

Endpoint for table lifecycle control operations (pause, resume, close).
Extracted from tables.py (SF-2) to keep that module below 500 lines.

CRITICAL: Control operations are ATOMIC - both the CONTROL saying append
and the table status update happen in a single transaction. This ensures
the audit trail and state remain consistent even on failure.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from returns.result import Failure

from tasca.core.domain.saying import Speaker, SpeakerKind
from tasca.shell.api.auth import verify_admin_token
from tasca.shell.api.deps import get_db
from tasca.shell.api.fastapi_compat import APIRouter, Depends, HTTPException, status
from tasca.shell.services.operations.table_control import (
    TableControlErrorCode,
    execute_table_control,
)

router = APIRouter()


# =============================================================================
# Request / Response Models
# =============================================================================


class TableControlRequest(BaseModel):
    """Request model for table control operations."""

    action: str = Field(..., description="Control action: pause, resume, or close")
    speaker_name: str = Field("Admin", description="Name of the speaker performing the action")
    reason: str | None = Field(None, description="Optional reason for the action")
    dedup_id: str | None = Field(None, description="Optional idempotency key accepted for HTTP API compatibility")


class TableControlResponse(BaseModel):
    """Response model for table control operations."""

    table_status: str
    control_saying_sequence: int


# =============================================================================
# POST /tables/{table_id}/control - Control table lifecycle (Admin required)
# =============================================================================


# @invar:allow entry_point_too_thick: tables_control.py control_table_endpoint route with docstrings, type hints, and error handling
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
    # Create speaker for CONTROL saying (human speaker, no patron_id)
    speaker = Speaker(kind=SpeakerKind.HUMAN, name=data.speaker_name)
    now = datetime.now(UTC)
    result = execute_table_control(conn, table_id, data.action, speaker, data.reason, now)

    if isinstance(result, Failure):
        error = result.failure()
        if error.code == TableControlErrorCode.INVALID_ACTION:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid action: {data.action}. Must be 'pause', 'resume', or 'close'.",
            )
        if error.code == TableControlErrorCode.TABLE_NOT_FOUND:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table not found: {table_id}",
            )
        if error.code == TableControlErrorCode.INVALID_TRANSITION:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error.message,
            )
        if error.code == TableControlErrorCode.VERSION_CONFLICT:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "version_conflict",
                    "table_id": table_id,
                    "expected_version": error.expected_version,
                    "actual_version": error.actual_version,
                    "actual_status": error.current_status.value if error.current_status else None,
                    "message": error.message,
                },
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to execute control operation: {error.message}",
        )

    outcome = result.unwrap()

    return TableControlResponse(
        table_status=outcome.table.status.value,
        control_saying_sequence=outcome.control_saying.sequence,
    )
