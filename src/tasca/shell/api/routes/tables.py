"""
Tables API routes.

Endpoints for table management operations.
Control lifecycle operations (pause/resume/close) are in tables_control.py.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from returns.result import Failure, Result, Success

from tasca.core.domain.patron import PatronId
from tasca.core.domain.seat import Seat, SeatId, SeatState
from tasca.core.domain.table import Table, TableId, TableUpdate, Version
from tasca.core.services.batch_delete_service import (
    MAX_BATCH_SIZE,
)
from tasca.core.services.seat_service import DEFAULT_SEAT_TTL_SECONDS, calculate_expiry_time
from tasca.core.table_state_machine import can_join
from tasca.shell.api.auth import verify_admin_token
from tasca.shell.api.deps import get_db
from tasca.shell.api.errors import error_envelope
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
from tasca.shell.storage.patron_repo import PatronNotFoundError, get_patron
from tasca.shell.storage.saying_repo import get_recent_sayings, get_table_max_sequence
from tasca.shell.storage.seat_repo import create_seat
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
logger = get_logger(__name__).unwrap()


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


class TableJoinRequest(BaseModel):
    """Request model for the documented REST table join surface."""

    invite_code: str | None = None
    table_id: str | None = None
    patron_id: str | None = None
    history_limit: int | None = Field(default=10, ge=1, le=200)
    history_max_bytes: int | None = Field(default=65536, ge=1)


class TableJoinInitialResponse(BaseModel):
    """Initial history block returned by table join."""

    sayings: list[dict[str, Any]]
    next_sequence: int
    has_more_history: bool


class TableJoinResponse(BaseModel):
    """REST table.join-compatible response."""

    table: dict[str, Any]
    sequence_latest: int
    history_sequence: int
    initial: TableJoinInitialResponse
    seat: dict[str, Any] | None = None


class BatchDeleteRejectionDetail(BaseModel):
    """Per-ID rejection detail for batch delete failures."""

    id: str
    reason: str


class BatchDeleteErrorResponse(BaseModel):
    """Response model for batch delete precondition failure."""

    error: str = "BATCH_PRECONDITION_FAILED"
    details: list[BatchDeleteRejectionDetail]


# @shell_complexity: REST table creation maps validation, idempotency, service errors, logging, and cache write.
def _table_create_response(
    conn: sqlite3.Connection,
    data: TableCreateRequest,
    now: datetime,
) -> Result[TableCreateResponse, HTTPException]:
    """Create a table and return the REST response model."""
    if data.title is None and data.question is None:
        return Failure(
            HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_envelope("InvalidRequest", "Either title or question is required"),
            )
        )
    resource_key = "table_create"
    if data.dedup_id is not None:
        cached_result = check_idempotency_key(conn, resource_key, "table_create", data.dedup_id, now=now)
        if isinstance(cached_result, Failure):
            return Failure(HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to check idempotency key: {cached_result.failure()}"))
        cached = cached_result.unwrap()
        if cached is not None:
            return Success(TableCreateResponse(**cached["data"]))
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
            return Failure(HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to generate table ID: {error.cause}"))
        if isinstance(error, TableCreateError):
            return Failure(HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create table: {error.cause}"))
        return Failure(HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create table: {error}"))
    outcome = result.unwrap()
    table = outcome.table
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
        store_result = store_idempotency_key(conn, resource_key, "table_create", data.dedup_id, {"data": response.model_dump(mode="json")}, now=now)
        if isinstance(store_result, Failure):
            return Failure(HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to store idempotency key: {store_result.failure()}"))
    return Success(response)


def _list_tables_response(conn: sqlite3.Connection) -> Result[list[Table], HTTPException]:
    """List tables or return an HTTP storage failure."""
    result = list_tables(conn)
    if isinstance(result, Failure):
        return Failure(HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to list tables: {result.failure()}"))
    return Success(result.unwrap())


def _get_table_response(conn: sqlite3.Connection, table_id: str) -> Result[Table, HTTPException]:
    """Fetch one table or return an HTTP lookup failure."""
    result = get_table(conn, TableId(table_id))
    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, TableNotFoundError):
            return Failure(HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Table not found: {table_id}"))
        return Failure(HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get table: {error}"))
    return Success(result.unwrap())


# @invar:allow shell_result: deterministic transport payload formatter; callers handle I/O failures before formatting.
# @shell_orchestration: REST wire-shape compatibility aliases belong to the HTTP adapter surface.
def _table_wire_payload(table: Table) -> dict[str, Any]:
    """Build public table payload used by REST join."""
    return {
        "id": table.id,
        "table_id": table.id,
        "question": table.question,
        "title": table.question,
        "context": table.context,
        "status": table.status.value,
        "version": table.version,
        "creator_patron_id": table.creator_patron_id,
        "creator_id": table.creator_patron_id,
        "created_by": table.creator_patron_id,
        "host_ids": table.host_ids,
        "metadata": table.metadata,
        "policy": table.policy,
        "board": table.board,
        "created_at": table.created_at.isoformat(),
        "updated_at": table.updated_at.isoformat(),
    }


# @invar:allow shell_result: deterministic transport payload formatter; callers handle I/O failures before formatting.
# @shell_orchestration: REST wire-shape formatting belongs to the HTTP adapter surface.
def _saying_wire_payload(saying: Any) -> dict[str, Any]:
    """Build public saying payload used by REST join history."""
    return {
        "id": saying.id,
        "table_id": saying.table_id,
        "sequence": saying.sequence,
        "speaker": {
            "kind": saying.speaker.kind.value,
            "name": saying.speaker.name,
            "patron_id": saying.speaker.patron_id,
        },
        "content": saying.content,
        "attachments": [item.model_dump(mode="json") for item in saying.attachments],
        "pinned": saying.pinned,
        "created_at": saying.created_at.isoformat(),
    }


def _create_join_seat(
    conn: sqlite3.Connection,
    table_id: str,
    patron_id: str,
    now: datetime,
) -> Result[dict[str, Any], HTTPException]:
    """Create a seat for a joining patron or return HTTP lookup/storage failure."""
    patron_result = get_patron(conn, PatronId(patron_id))
    if isinstance(patron_result, Failure):
        error = patron_result.failure()
        if isinstance(error, PatronNotFoundError):
            return Failure(HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_envelope("TableNotFound", f"Patron not found: {patron_id}")))
        return Failure(HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get patron: {error}"))
    seat = Seat(
        id=SeatId(str(uuid.uuid4())),
        table_id=table_id,
        patron_id=patron_id,
        state=SeatState.JOINED,
        last_heartbeat=now,
        joined_at=now,
    )
    seat_result = create_seat(conn, seat)
    if isinstance(seat_result, Failure):
        return Failure(HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create seat: {seat_result.failure()}"))
    created = seat_result.unwrap()
    expires_at = calculate_expiry_time(created.last_heartbeat, DEFAULT_SEAT_TTL_SECONDS)
    return Success({
        "id": created.id,
        "table_id": created.table_id,
        "patron_id": created.patron_id,
        "state": created.state.value,
        "last_heartbeat": created.last_heartbeat.isoformat(),
        "joined_at": created.joined_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    })


# @shell_complexity: REST join adapter maps lookup, state guard, history, optional seat, and HTTP errors.
def _table_join_response(
    conn: sqlite3.Connection,
    data: TableJoinRequest,
    now: datetime,
) -> Result[TableJoinResponse, HTTPException]:
    """Join a table through the documented REST public route."""
    resolved_table_id = data.invite_code or data.table_id
    if resolved_table_id is None:
        return Failure(HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_envelope("InvalidRequest", "Either invite_code or table_id is required")))
    table_result = _get_table_response(conn, resolved_table_id)
    if isinstance(table_result, Failure):
        return Failure(table_result.failure())
    table = table_result.unwrap()
    if not can_join(table.status):
        return Failure(HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error_envelope(
                "InvalidState",
                f"Cannot join table with status '{table.status.value}'. Only OPEN tables accept new joins.",
                {"table_status": table.status.value},
            ),
        ))
    max_seq_result = get_table_max_sequence(conn, resolved_table_id)
    if isinstance(max_seq_result, Failure):
        return Failure(HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get table sequence: {max_seq_result.failure()}"))
    sequence_latest = max(0, max_seq_result.unwrap())
    history_result = get_recent_sayings(
        conn,
        resolved_table_id,
        limit=data.history_limit or 10,
        max_bytes=data.history_max_bytes or 65536,
    )
    if isinstance(history_result, Failure):
        return Failure(HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get history: {history_result.failure()}"))
    history_sayings, history_sequence, has_more_history = history_result.unwrap()
    if history_sayings:
        next_sequence = max(s.sequence for s in history_sayings)
        public_history_sequence = history_sequence
    else:
        next_sequence = 0
        public_history_sequence = 0
    seat_data = None
    if data.patron_id is not None:
        seat_result = _create_join_seat(conn, resolved_table_id, data.patron_id, now)
        if isinstance(seat_result, Failure):
            return Failure(seat_result.failure())
        seat_data = seat_result.unwrap()
    return Success(TableJoinResponse(
        table=_table_wire_payload(table),
        sequence_latest=sequence_latest,
        history_sequence=public_history_sequence,
        initial=TableJoinInitialResponse(
            sayings=[_saying_wire_payload(s) for s in history_sayings],
            next_sequence=next_sequence,
            has_more_history=has_more_history,
        ),
        seat=seat_data,
    ))


def _update_table_response(
    conn: sqlite3.Connection,
    table_id: str,
    data: TableUpdate,
    expected_version: int,
    now: datetime,
) -> Result[Table, HTTPException]:
    """Update a table with optimistic concurrency semantics."""
    current_result = _get_table_response(conn, table_id)
    if isinstance(current_result, Failure):
        return Failure(current_result.failure())
    if data.status != current_result.unwrap().status:
        return Failure(HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="status changes are not allowed via PUT. Use POST /tables/{table_id}/control instead."))
    result = update_table(conn=conn, table_id=TableId(table_id), update=data, expected_version=Version(expected_version), now=now)
    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, TableNotFoundError):
            return Failure(HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Table not found: {table_id}"))
        if isinstance(error, VersionConflictError):
            return Failure(HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error.to_json()))
        return Failure(HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update table: {error}"))
    table = result.unwrap()
    log_table_update(logger, table.id, table.version, "rest:admin")
    return Success(table)


def _delete_table_response(conn: sqlite3.Connection, table_id: str) -> Result[DeleteResponse, HTTPException]:
    """Delete a table and return the REST confirmation."""
    result = delete_table(conn, TableId(table_id))
    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, TableNotFoundError):
            return Failure(HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Table not found: {table_id}"))
        return Failure(HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete table: {error}"))
    log_table_delete(logger, table_id, "rest:admin")
    return Success(DeleteResponse(status="deleted", table_id=table_id))


def _batch_delete_tables_response(
    conn: sqlite3.Connection,
    ids: list[str],
) -> Result[BatchDeleteResponse, HTTPException]:
    """Batch-delete tables and return all-or-nothing REST response."""
    delete_result = delete_tables_batch(conn, ids)
    if isinstance(delete_result, Failure):
        failure = delete_result.failure()
        if failure.status == "invalid_request":
            return Failure(HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=failure.error or f"ids must contain 1 to {failure.max_batch_size} table IDs."))
        if failure.status == "precondition_failed":
            return Failure(HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "BATCH_PRECONDITION_FAILED", "details": failure.rejection_details}))
        return Failure(HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to batch delete tables: {failure.error}"))
    deleted_ids = delete_result.unwrap().deleted_ids
    log_batch_table_delete(logger, deleted_ids, "rest:admin")
    return Success(BatchDeleteResponse(deleted_count=len(deleted_ids), failed=[], deleted_ids=deleted_ids))


# =============================================================================
# POST /tables - Create a new table (Admin required)
# =============================================================================


@router.post("", response_model=TableCreateResponse, status_code=status.HTTP_200_OK)
async def create_table_endpoint(
    data: TableCreateRequest,
    _auth: None = Depends(verify_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
) -> TableCreateResponse:
    """Create a new discussion table."""
    result = _table_create_response(conn, data, datetime.now(UTC))
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()


# =============================================================================
# GET /tables - List all tables
# =============================================================================


@router.get("", response_model=list[Table])
async def list_tables_endpoint(
    conn: sqlite3.Connection = Depends(get_db),
) -> list[Table]:
    """List all tables."""
    result = _list_tables_response(conn)
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()


# =============================================================================
# GET /tables/{table_id} - Get a table by ID
# =============================================================================


@router.post("/join", response_model=TableJoinResponse, status_code=status.HTTP_200_OK)
async def join_table_endpoint(
    data: TableJoinRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> TableJoinResponse:
    """Join a table and return initial history via the public REST route."""
    result = _table_join_response(conn, data, datetime.now(UTC))
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()


# =============================================================================
# GET /tables/{table_id} - Get a table by ID
# =============================================================================


@router.get("/{table_id}", response_model=Table)
async def get_table_endpoint(
    table_id: str,
    conn: sqlite3.Connection = Depends(get_db),
) -> Table:
    """Get a table by ID."""
    result = _get_table_response(conn, table_id)
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()


# =============================================================================
# PUT /tables/{table_id} - Update a table (Admin required)
# =============================================================================


@router.put("/{table_id}", response_model=Table)
async def update_table_endpoint(
    table_id: str,
    data: TableUpdate,
    expected_version: int = Query(..., description="Expected version for optimistic concurrency"),
    _auth: None = Depends(verify_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
) -> Table:
    """Update a table with optimistic concurrency control."""
    result = _update_table_response(conn, table_id, data, expected_version, datetime.now(UTC))
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()


# =============================================================================
# DELETE /tables/{table_id} - Delete a table (Admin required)
# =============================================================================


@router.delete("/{table_id}", response_model=DeleteResponse)
async def delete_table_endpoint(
    table_id: str,
    _auth: None = Depends(verify_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
) -> DeleteResponse:
    """Delete a table by ID."""
    result = _delete_table_response(conn, table_id)
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()


# =============================================================================
# POST /tables/actions/batch-delete - Batch delete tables (Admin required)
# =============================================================================


@router.post("/actions/batch-delete", response_model=BatchDeleteResponse)
async def batch_delete_tables_endpoint(
    data: BatchDeleteRequest,
    _auth: None = Depends(verify_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
) -> BatchDeleteResponse:
    """Batch delete tables with all-or-nothing semantics."""
    result = _batch_delete_tables_response(conn, data.ids)
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()
