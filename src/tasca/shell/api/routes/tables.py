"""
Tables API routes.

Endpoints for table management operations.
Control lifecycle operations (pause/resume/close) are in tables_control.py.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from returns.result import Failure

from tasca.core.domain.table import Table, TableId, TableUpdate, Version
from tasca.core.services.batch_delete_service import (
    MAX_BATCH_SIZE,
)
from tasca.shell.api.auth import verify_admin_token
from tasca.shell.api.deps import get_db
from tasca.shell.api.errors import raise_http_error
from tasca.shell.api.fastapi_compat import APIRouter, Depends, HTTPException, Query, status
from tasca.shell.api.routes import tables_control
from tasca.shell.logging import (
    get_logger,
    log_batch_table_delete,
    log_table_create,
    log_table_delete,
    log_table_update,
)
from tasca.shell.services.operations.batch_delete import delete_tables_batch
from tasca.shell.services.operations.table_creation import (
    TableCreateError,
    TableIdSelectionError,
    create_discussion_table,
)
from tasca.shell.storage.idempotency_repo import check_idempotency_key, store_idempotency_key
from tasca.shell.storage.table_repo import (
    TableNotFoundError,
    VersionConflictError,
    delete_table,
    get_table,
    list_tables,
    update_table,
)

router = APIRouter()
router.include_router(tables_control.router)
logger = get_logger(__name__)


# =============================================================================
# Response Models
# =============================================================================


class DeleteResponse(BaseModel):
    """Response model for delete operations."""

    status: str
    table_id: str


class BatchDeleteRequest(BaseModel):
    """Request model for batch delete operations."""

    ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_BATCH_SIZE,
        description=f"Table IDs to delete (1-{MAX_BATCH_SIZE})",
    )


class BatchDeleteResponse(BaseModel):
    """Response model for successful batch delete."""

    deleted_count: int
    failed: list[dict[str, str]] = Field(default_factory=list)
    deleted_ids: list[str]


class TableCreateRequest(BaseModel):
    """REST request model matching table.create plus legacy aliases."""

    title: str | None = None
    question: str | None = None
    context: str | None = None
    created_by: str | None = None
    creator_patron_id: str | None = None
    host_ids: list[str] | None = None
    metadata: dict[str, object] | None = None
    policy: dict[str, object] | None = None
    board: dict[str, object] | None = None
    dedup_id: str | None = None


class TableCreateResponse(BaseModel):
    """REST response model with canonical and compatibility table fields."""

    table_id: str
    invite_code: str
    web_url: str
    status: str
    version: int
    creator_id: str | None = None
    host_ids: list[str]
    title: str
    created_by: str | None = None
    metadata: dict[str, object]
    policy: dict[str, object]
    board: dict[str, object]
    id: str
    question: str
    context: str | None = None
    created_at: datetime
    updated_at: datetime


class BatchDeleteRejectionDetail(BaseModel):
    """Per-ID rejection detail for batch delete failures."""

    id: str
    reason: str


class BatchDeleteErrorResponse(BaseModel):
    """Response model for batch delete precondition failure."""

    error: str = "BATCH_PRECONDITION_FAILED"
    details: list[BatchDeleteRejectionDetail]


# =============================================================================
# POST /tables - Create a new table (Admin required)
# =============================================================================


# @invar:allow entry_point_too_thick: tables.py create_table_endpoint POST route with docstrings, type hints, and error handling
@router.post("", response_model=TableCreateResponse, status_code=status.HTTP_200_OK)
async def create_table_endpoint(
    data: TableCreateRequest,
    _auth: None = Depends(verify_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
) -> TableCreateResponse:
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
    if data.title is None and data.question is None:
        raise_http_error(
            status.HTTP_400_BAD_REQUEST,
            "InvalidRequest",
            "Either title or question is required",
        )
    resource_key = "table_create"
    now = datetime.now(UTC)
    if data.dedup_id is not None:
        idempotency_result = check_idempotency_key(conn, resource_key, "table_create", data.dedup_id, now=now)
        if isinstance(idempotency_result, Failure):
            raise_http_error(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "StorageError",
                f"Failed to check idempotency key: {idempotency_result.failure()}",
            )
        cached = idempotency_result.unwrap()
        if cached is not None:
            return TableCreateResponse(**cached["data"])

    result = create_discussion_table(
        conn,
        data.question,
        title=data.title,
        context=data.context,
        creator_patron_id=data.creator_patron_id,
        created_by=data.created_by,
        host_ids=data.host_ids,
        metadata=data.metadata,
        policy=data.policy,
        board=data.board,
        now=now,
    )

    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, TableIdSelectionError):
            raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "StorageError", f"Failed to generate table ID: {error.cause}")
        if isinstance(error, TableCreateError):
            raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "StorageError", f"Failed to create table: {error.cause}")
        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "StorageError", f"Failed to create table: {error}")

    outcome = result.unwrap()
    table = outcome.table

    # Log table creation
    log_table_create(logger, table.id, "rest:admin")

    response = TableCreateResponse(
        table_id=table.id,
        invite_code=outcome.invite_code,
        web_url=outcome.web_url,
        status=table.status.value,
        version=table.version,
        creator_id=table.creator_patron_id,
        host_ids=outcome.host_ids,
        title=table.question,
        created_by=table.creator_patron_id,
        metadata=outcome.metadata,
        policy=outcome.policy,
        board=outcome.board,
        id=table.id,
        question=table.question,
        context=table.context,
        created_at=table.created_at,
        updated_at=table.updated_at,
    )
    if data.dedup_id is not None:
        store_result = store_idempotency_key(
            conn,
            resource_key,
            "table_create",
            data.dedup_id,
            {"data": response.model_dump(mode="json")},
            now=now,
        )
        if isinstance(store_result, Failure):
            raise_http_error(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "StorageError",
                f"Failed to store idempotency key: {store_result.failure()}",
            )
    return response


# =============================================================================
# GET /tables - List all tables
# =============================================================================


# @invar:allow entry_point_too_thick: tables.py create_table_endpoint POST route with docstrings, type hints, and error handling
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


# @invar:allow entry_point_too_thick: tables.py create_table_endpoint POST route with docstrings, type hints, and error handling
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


# @invar:allow entry_point_too_thick: tables.py create_table_endpoint POST route with docstrings, type hints, and error handling
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

    **Full Replace Semantics:**
    - ALL fields are required (question, context, status)
    - To clear context: provide `context: null`
    - To keep context: provide the current context value
    - Omitting a field will cause a 422 validation error

    Note: Status changes are NOT allowed via this endpoint.
    Use POST /tables/{table_id}/control for status changes.

    Optimistic Concurrency:
    - Client must provide expected_version (the version they last saw)
    - Server checks current version matches expected_version
    - On conflict, returns 409 Conflict with version details

    Args:
        table_id: The table identifier.
        data: Full replacement data - ALL fields required.
        expected_version: Version the client expects (optimistic concurrency).
        _auth: Admin authentication (injected via dependency).
        conn: Database connection (injected via dependency).

    Returns:
        The updated table with incremented version.

    Raises:
        HTTPException: 400 if status change attempted.
        HTTPException: 404 if table not found.
        HTTPException: 409 if version conflict (optimistic concurrency).
        HTTPException: 422 if required fields missing.
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

    # Note: Optimistic concurrency (expected_version) provides the actual consistency
    # guarantee; this check is an early rejection hint only.
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


# @invar:allow entry_point_too_thick: tables.py create_table_endpoint POST route with docstrings, type hints, and error handling
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
# POST /tables/actions/batch-delete - Batch delete tables (Admin required)
# =============================================================================


# @invar:allow entry_point_too_thick: FastAPI route with validation + cascade delete orchestration
@router.post("/actions/batch-delete", response_model=BatchDeleteResponse)
async def batch_delete_tables_endpoint(
    data: BatchDeleteRequest,
    _auth: None = Depends(verify_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
) -> BatchDeleteResponse:
    """Batch delete tables with all-or-nothing semantics.

    All requested tables must exist and be in CLOSED status.
    If any table fails validation, the entire batch is rejected.

    Cascade: deletes associated seats and sayings in a single transaction.

    Requires admin authentication via Bearer token.

    Args:
        data: Batch delete request with table IDs.
        _auth: Admin authentication (injected via dependency).
        conn: Database connection (injected via dependency).

    Returns:
        BatchDeleteResponse with deleted_ids on success.

    Raises:
        HTTPException: 401 if not authenticated.
        HTTPException: 409 if any table is not closed or not found.
        HTTPException: 422 if ids list is empty or exceeds limit.
        HTTPException: 500 if database operation fails.
    """
    delete_result = delete_tables_batch(conn, data.ids)

    if isinstance(delete_result, Failure):
        failure = delete_result.failure()
        if failure.status == "invalid_request":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=failure.error or f"ids must contain 1 to {failure.max_batch_size} table IDs.",
            )
        if failure.status == "precondition_failed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "BATCH_PRECONDITION_FAILED",
                    "details": failure.rejection_details,
                },
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to batch delete tables: {failure.error}",
        )

    deleted_ids = delete_result.unwrap().deleted_ids

    # Log batch deletion
    log_batch_table_delete(logger, deleted_ids, "rest:admin")

    return BatchDeleteResponse(deleted_count=len(deleted_ids), failed=[], deleted_ids=deleted_ids)
