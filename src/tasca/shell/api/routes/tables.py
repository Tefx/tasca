"""
Tables API routes.

Endpoints for table management operations.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from returns.result import Failure, Success

from tasca.core.domain.saying import Speaker, SpeakerKind
from tasca.core.domain.table import Table, TableCreate, TableId, TableStatus, TableUpdate, Version
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
from tasca.shell.logging import get_logger, log_table_create, log_table_delete, log_table_update
from tasca.shell.services.table_id_generator import (
    TableIdGenerationError,
    generate_table_id,
)
from tasca.shell.storage.saying_repo import append_saying
from tasca.shell.storage.table_repo import (
    TableNotFoundError,
    create_table,
    delete_table,
    get_table,
    list_tables,
    update_table,
    VersionConflictError,
)

router = APIRouter()
logger = get_logger(__name__)


# =============================================================================
# Response Models
# =============================================================================


class DeleteResponse(BaseModel):
    """Response model for delete operations."""

    status: str
    table_id: str


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
# POST /tables - Create a new table (Admin required)
# =============================================================================


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
@router.post("", response_model=Table, status_code=status.HTTP_200_OK)
async def create_table_endpoint(
    data: TableCreate,
    _auth: None = Depends(verify_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
) -> Table:
    """Create a new discussion table.

    Requires admin authentication via Bearer token.

    Args:
        data: Table creation data with question and optional context.
        _auth: Admin authentication (injected via dependency).
        conn: Database connection (injected via dependency).

    Returns:
        The created table with generated ID, version 1, and timestamps.

    Raises:
        HTTPException: 500 if database operation fails.
    """
    now = datetime.now(UTC)
    table_id_result = generate_table_id(conn)

    if isinstance(table_id_result, Failure):
        error = table_id_result.failure()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate table ID: {error}",
        )

    table_id = table_id_result.unwrap()

    table = Table(
        id=table_id,
        question=data.question,
        context=data.context,
        status=TableStatus.OPEN,
        version=Version(1),
        created_at=now,
        updated_at=now,
    )

    result = create_table(conn, table)

    if isinstance(result, Failure):
        error = result.failure()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create table: {error}",
        )

    table = result.unwrap()

    # Log table creation
    log_table_create(logger, table.id, "rest:admin")

    return table


# =============================================================================
# GET /tables - List all tables
# =============================================================================


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
@router.get("", response_model=list[Table])
async def list_tables_endpoint(
    conn: sqlite3.Connection = Depends(get_db),
) -> list[Table]:
    """List all tables.

    Args:
        conn: Database connection (injected via dependency).

    Returns:
        List of all tables ordered by creation date (newest first).
    """
    result = list_tables(conn)

    if isinstance(result, Failure):
        error = result.failure()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list tables: {error}",
        )

    return result.unwrap()


# =============================================================================
# GET /tables/{table_id} - Get a table by ID
# =============================================================================


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
@router.get("/{table_id}", response_model=Table)
async def get_table_endpoint(
    table_id: str,
    conn: sqlite3.Connection = Depends(get_db),
) -> Table:
    """Get a table by ID.

    Args:
        table_id: The table identifier.
        conn: Database connection (injected via dependency).

    Returns:
        The requested table.

    Raises:
        HTTPException: 404 if table not found.
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


# =============================================================================
# PUT /tables/{table_id} - Update a table (Admin required)
# =============================================================================


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
@router.put("/{table_id}", response_model=Table)
async def update_table_endpoint(
    table_id: str,
    data: TableUpdate,
    expected_version: int = Query(..., description="Expected version for optimistic concurrency"),
    _auth: None = Depends(verify_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
) -> Table:
    """Update a table with optimistic concurrency control.

    Uses replace-only semantics: all updatable fields must be provided.
    Requires admin authentication via Bearer token.

    Note: Status changes are NOT allowed via this endpoint.
    Use POST /tables/{table_id}/control for status changes.

    Optimistic Concurrency:
    - Client must provide expected_version (the version they last saw)
    - Server checks current version matches expected_version
    - On conflict, returns 409 Conflict with version details

    Args:
        table_id: The table identifier.
        data: Full replacement data (question, context, status).
        expected_version: Version the client expects (optimistic concurrency).
        _auth: Admin authentication (injected via dependency).
        conn: Database connection (injected via dependency).

    Returns:
        The updated table with incremented version.

    Raises:
        HTTPException: 400 if status change attempted.
        HTTPException: 404 if table not found.
        HTTPException: 409 if version conflict (optimistic concurrency).
        HTTPException: 500 if database operation fails.
    """
    # First, fetch the current table to check status
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

    # Pre-flight check: status changes are not allowed via PUT
    if data.status != current_table.status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status changes are not allowed via PUT. Use POST /tables/{table_id}/control instead.",
        )

    now = datetime.now(UTC)

    result = update_table(
        conn=conn,
        table_id=TableId(table_id),
        update=data,
        expected_version=Version(expected_version),
        now=now,
    )

    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, TableNotFoundError):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table not found: {table_id}",
            )
        if isinstance(error, VersionConflictError):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=error.to_json(),
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update table: {error}",
        )

    table = result.unwrap()

    # Log table update
    log_table_update(logger, table.id, table.version, "rest:admin")

    return table


# =============================================================================
# DELETE /tables/{table_id} - Delete a table (Admin required)
# =============================================================================


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
@router.delete("/{table_id}", response_model=DeleteResponse)
async def delete_table_endpoint(
    table_id: str,
    _auth: None = Depends(verify_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
) -> DeleteResponse:
    """Delete a table by ID.

    Requires admin authentication via Bearer token.

    Args:
        table_id: The table identifier.
        _auth: Admin authentication (injected via dependency).
        conn: Database connection (injected via dependency).

    Returns:
        Confirmation of deletion.

    Raises:
        HTTPException: 404 if table not found.
        HTTPException: 500 if database operation fails.
    """
    result = delete_table(conn, TableId(table_id))

    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, TableNotFoundError):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table not found: {table_id}",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete table: {error}",
        )

    # Log table deletion
    log_table_delete(logger, table_id, "rest:admin")

    return DeleteResponse(status="deleted", table_id=table_id)


# =============================================================================
# POST /tables/{table_id}/control - Control table lifecycle (Admin required)
# =============================================================================


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
# @invar:allow shell_result: HTTP route returns response model, not Result
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
    - pause: OPEN → PAUSED
    - resume: PAUSED → OPEN
    - close: OPEN|PAUSED → CLOSED (terminal)

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
