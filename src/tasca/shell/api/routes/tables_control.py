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
from returns.result import Failure, Result, Success

from tasca.core.domain.saying import Speaker, SpeakerKind
from tasca.shell.api.auth import verify_admin_token
from tasca.shell.api.deps import get_db
from tasca.shell.api.fastapi_compat import APIRouter, Depends, HTTPException, status
from tasca.shell.services.operations.table_control import (
    TableControlErrorCode,
    TableControlOperationError,
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


# @shell_complexity: Exhaustive transport mapping keeps table.control HTTP status compatibility explicit.
def _control_error_to_http(
    table_id: str,
    action: str,
    error: TableControlOperationError,
) -> Result[HTTPException, str]:
    """Map table-control service errors to stable HTTP responses."""
    code = error.code
    if code == TableControlErrorCode.INVALID_ACTION:
        return Success(HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid action: {action}. Must be 'pause', 'resume', or 'close'.",
        ))
    if code == TableControlErrorCode.TABLE_NOT_FOUND:
        return Success(HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Table not found: {table_id}"))
    if code == TableControlErrorCode.INVALID_TRANSITION:
        return Success(HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error.message))
    if code == TableControlErrorCode.VERSION_CONFLICT:
        current_status = error.current_status
        return Success(HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "version_conflict",
                "table_id": table_id,
                "expected_version": error.expected_version,
                "actual_version": error.actual_version,
                "actual_status": current_status.value if current_status else None,
                "message": error.message,
            },
        ))
    return Success(HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Failed to execute control operation: {error.message}",
    ))


def _control_table_response(
    conn: sqlite3.Connection,
    table_id: str,
    data: TableControlRequest,
    now: datetime,
) -> Result[TableControlResponse, HTTPException]:
    """Execute table control and build the HTTP response envelope."""
    speaker = Speaker(kind=SpeakerKind.HUMAN, name=data.speaker_name)
    result = execute_table_control(conn, table_id, data.action, speaker, data.reason, now)
    if isinstance(result, Failure):
        return Failure(_control_error_to_http(table_id, data.action, result.failure()).unwrap())
    outcome = result.unwrap()
    return Success(TableControlResponse(
        table_status=outcome.table.status.value,
        control_saying_sequence=outcome.control_saying.sequence,
    ))


# =============================================================================
# POST /tables/{table_id}/control - Control table lifecycle (Admin required)
# =============================================================================


@router.post("/{table_id}/control", response_model=TableControlResponse)
async def control_table_endpoint(
    table_id: str,
    data: TableControlRequest,
    _auth: None = Depends(verify_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
) -> TableControlResponse:
    """Control table lifecycle: pause, resume, or close."""
    result = _control_table_response(conn, table_id, data, datetime.now(UTC))
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()
