"""
Tables API routes.

Endpoints for table management operations.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from returns.result import Failure, Success

from tasca.core.domain.table import Table, TableCreate, TableId, TableStatus, TableUpdate, Version
from tasca.shell.api.auth import verify_admin_token
from tasca.shell.api.deps import get_db
from tasca.shell.services.table_id_generator import (
    TableIdGenerationError,
    generate_table_id,
)
from tasca.shell.storage.table_repo import (
    TableNotFoundError,
    create_table,
    delete_table,
    get_table,
    list_tables,
    update_table,
    VersionConflictError,
)
from tasca.shell.logging import get_logger, log_table_create, log_table_delete, log_table_update

router = APIRouter()
logger = get_logger(__name__)


# =============================================================================
# Response Models
# =============================================================================


class DeleteResponse(BaseModel):
    """Response model for delete operations."""

    status: str
    table_id: str


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
