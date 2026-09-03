# @invar:allow file_size: MCP tool orchestration extracted from server entrypoints
"""MCP tool entrypoint implementations extracted from server module."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ValidationError
from returns.result import Failure, Result, Success

from tasca.core.domain.patron import Patron, PatronId
from tasca.core.domain.saying import AttachmentInput, Speaker, SpeakerKind
from tasca.core.domain.seat import Seat, SeatId, SeatState
from tasca.core.domain.table import TableId, Version
from tasca.core.services.limits_service import LimitsConfig, settings_to_limits_config
from tasca.core.services.mention_service import (
    PatronMatch,
    has_ambiguous_mentions,
    resolve_mentions,
)
from tasca.core.services.seat_service import (
    DEFAULT_SEAT_TTL_SECONDS,
    calculate_expiry_time,
)
from tasca.core.table_state_machine import (
    can_join,
)
from tasca.shell.logging import (
    get_logger,
    log_batch_table_delete,
    log_dedup_hit,
    log_say,
    log_table_create,
)
from tasca.shell.mcp.database import get_mcp_db
from tasca.shell.mcp.entrypoint_logic import (
    apply_table_patch as _apply_table_patch,
)
from tasca.shell.mcp.entrypoint_logic import (
    build_control_response as _build_control_response,
)
from tasca.shell.mcp.entrypoint_logic import (
    build_join_next_action as _build_join_next_action,
)
from tasca.shell.mcp.entrypoint_logic import (
    build_patron_response_data as _build_patron_response_data,
)
from tasca.shell.mcp.entrypoint_logic import (
    build_say_response as _build_say_response,
)
from tasca.shell.mcp.entrypoint_logic import (
    build_seat_dict as _build_seat_dict,
)
from tasca.shell.mcp.entrypoint_logic import (
    build_table_dict as _build_table_dict,
)
from tasca.shell.mcp.entrypoint_logic import (
    build_table_say_compat_metadata as _build_table_say_compat_metadata,
)
from tasca.shell.mcp.entrypoint_logic import (
    build_table_update_actor_metadata as _build_table_update_actor_metadata,
)
from tasca.shell.mcp.entrypoint_logic import (
    compute_next_sequence as _compute_next_sequence,
)
from tasca.shell.mcp.entrypoint_logic import (
    format_saying_dict as _format_saying_dict,
)
from tasca.shell.mcp.entrypoint_logic import (
    limit_error_to_response as _limit_error_to_response,
)
from tasca.shell.mcp.entrypoint_logic import (
    silence_next_action as _silence_next_action,
)
from tasca.shell.mcp.entrypoint_session_tools import (
    connect_impl as _connect_impl,
)
from tasca.shell.mcp.entrypoint_session_tools import (
    connection_status_impl as _connection_status_impl,
)
from tasca.shell.mcp.entrypoint_session_tools import (
    seat_heartbeat_impl as _seat_heartbeat_impl,
)
from tasca.shell.mcp.entrypoint_session_tools import (
    seat_list_impl as _seat_list_impl,
)
from tasca.shell.mcp.responses import error_response, success_response
from tasca.shell.services.limited_saying_service import (
    TableSayError,
    TableSayErrorKind,
    append_saying_operation,
    validate_table_say_speaker_constraints,
)
from tasca.shell.services.operations.batch_delete import delete_tables_batch
from tasca.shell.services.operations.patron_registration import (
    PatronCreateError,
    PatronIdempotencyError,
    PatronLookupError,
)
from tasca.shell.services.operations.patron_registration import (
    register_patron as register_patron_operation,
)
from tasca.shell.services.operations.table_control import (
    TableControlErrorCode,
    execute_table_control,
)
from tasca.shell.services.operations.table_creation import (
    create_discussion_table,
)
from tasca.shell.services.operations.table_export import export_table
from tasca.shell.storage.attachment_repo import get_attachments_by_ids
from tasca.shell.storage.idempotency_repo import check_idempotency_key, store_idempotency_key
from tasca.shell.storage.patron_repo import (
    PatronNotFoundError,
    create_patron,
    find_patron_by_name,
    get_patron,
    list_patrons,
)
from tasca.shell.storage.saying_repo import (
    get_recent_sayings,
    get_table_max_sequence,
    list_sayings_by_table,
)
from tasca.shell.storage.seat_repo import (
    create_seat,
)
from tasca.shell.storage.table_repo import (
    TableNotFoundError,
    VersionConflictError,
    get_table,
    list_tables,
    list_tables_with_seat_counts,
    update_table,
)

logger = get_logger(__name__).unwrap()
McpEnvelope = dict[str, Any]
_TABLE_SAY_LOCK = threading.RLock()


# Per-session loop state tracking.
# MCP server runs per-agent (stdio) or per-session (HTTP), so module-level state
# is scoped to a single agent's session. Keyed by table_id.
_loop_state: dict[str, dict[str, int]] = {}


# @shell_orchestration: Session loop state is MCP runtime orchestration state.
def _get_loop_state(table_id: str) -> Result[dict[str, int], McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    if table_id not in _loop_state:
        _loop_state[table_id] = {
            "consecutive_empty_waits": 0,
            "total_iterations": 0,
        }
    return Success(_loop_state[table_id])


# @shell_orchestration: Session loop counters must remain in MCP shell state.
def _record_wait_result(table_id: str, *, got_sayings: bool) -> Result[dict[str, int], McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    state_result = _get_loop_state(table_id)
    state = state_result.unwrap()
    state["total_iterations"] += 1
    if got_sayings:
        state["consecutive_empty_waits"] = 0
    else:
        state["consecutive_empty_waits"] += 1
    return Success(state)


# @shell_orchestration: Shell-local adapter from settings to MCP limits behavior.
def _limits_config_from_settings() -> Result[LimitsConfig, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    from tasca.config import settings as _settings  # Lazy import for test monkeypatching

    config = settings_to_limits_config(_settings)
    if config.max_content_length is None:
        return Success(LimitsConfig(
            max_sayings_per_table=config.max_sayings_per_table,
            max_content_length=65536,
            max_bytes_per_table=config.max_bytes_per_table,
            max_mentions_per_saying=config.max_mentions_per_saying,
        ))
    return Success(config)


# @shell_orchestration: Permission preflight requires DB lookup before mutation in MCP adapter.
# @shell_complexity: Auth, not-found, database, and denial branches are kept before mutation for atomicity.
def _authorize_table_mutation(conn: Any, table_id: str, patron_id: str | None) -> Result[None, McpEnvelope]:
    """Allow table creator or human-admin (no patron_id) to control/update a table."""
    if patron_id is None:
        return Success(None)
    table_result = get_table(conn, TableId(table_id))
    if isinstance(table_result, Failure):
        error = table_result.failure()
        if isinstance(error, TableNotFoundError):
            return Failure(error_response("NOT_FOUND", f"Table not found: {table_id}"))
        return Failure(error_response("DATABASE_ERROR", f"Failed to get table: {error}"))
    table = table_result.unwrap()
    if table.creator_patron_id == patron_id or patron_id in table.host_ids:
        return Success(None)
    return Failure(error_response(
        "PERMISSION_DENIED",
        "Actor is not authorized to control or update this table",
        {"table_id": table_id, "patron_id": patron_id},
    ))


# =============================================================================
# Patron Tools
# =============================================================================


# @shell_complexity: 10 branches for patron dedup check + create + idempotency store + error paths + backward compat
def patron_register(
    display_name: str | None = None,
    alias: str | None = None,
    meta: dict[str, Any] | None = None,
    patron_id: str | None = None,
    dedup_id: str | None = None,
    # Backward compatibility: accept 'name' as alias for display_name
    name: str | None = None,
    kind: str = "agent",
) -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    # Backward compatibility: fall back to 'name' if display_name not provided
    resolved_name = display_name or name
    if resolved_name is None:
        return Failure(error_response(
            "INVALID_REQUEST",
            "display_name (or name for backward compat) is required",
        ))

    conn = next(get_mcp_db())
    now = datetime.now(UTC)
    result = register_patron_operation(
        conn,
        resolved_name,
        kind=kind,
        alias=alias,
        meta=meta,
        patron_id=patron_id,
        dedup_id=dedup_id,
        now=now,
    )
    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, PatronIdempotencyError | PatronLookupError | PatronCreateError):
            return Failure(error_response("DATABASE_ERROR", str(error)))
        return Failure(error_response("DATABASE_ERROR", f"Failed to register patron: {error}"))

    outcome = result.unwrap()
    if dedup_id is not None and not outcome.is_new:
        log_dedup_hit(logger, "patron_register", "patron_register", dedup_id)
    response_data = _build_patron_response_data(outcome.patron, is_new=outcome.is_new)
    return Success(success_response(response_data))


def patron_get(patron_id: str) -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())
    result = get_patron(conn, PatronId(patron_id))

    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, PatronNotFoundError):
            return Failure(error_response("NOT_FOUND", f"Patron not found: {patron_id}"))
        return Failure(error_response("DATABASE_ERROR", f"Failed to get patron: {error}"))

    patron = result.unwrap()
    return Success(success_response(
        {
            # Spec-compliant nested structure
            "patron": {
                "patron_id": patron.id,
                "display_name": patron.name,
                "alias": patron.alias,
                "meta": patron.meta,
            },
            # Backward-compatible flat fields (not in spec)
            "id": patron.id,
            "name": patron.name,
            "kind": patron.kind,
            "alias": patron.alias,
            "meta": patron.meta,
            "created_at": patron.created_at.isoformat(),
        }
    ))


# =============================================================================
# Table Tools
# =============================================================================


# @shell_complexity: 5 branches for table creation + dedup store + idempotency + error handling
def table_create(
    question: str | None = None,
    context: str | None = None,
    creator_patron_id: str | None = None,
    dedup_id: str | None = None,
    *,
    title: str | None = None,
    created_by: str | None = None,
    host_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    board: dict[str, Any] | None = None,
) -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())

    # Resource key for idempotency scope (table_create uses global scope via dedup_id)
    resource_key = "table_create"

    # Check idempotency key if provided
    if dedup_id is not None:
        idempotency_result = check_idempotency_key(conn, resource_key, "table_create", dedup_id)
        if isinstance(idempotency_result, Failure):
            error = idempotency_result.failure()
            return Failure(error_response("DATABASE_ERROR", f"Failed to check idempotency key: {error}"))

        cached_response = idempotency_result.unwrap()
        if cached_response is not None:
            # Log dedup hit
            log_dedup_hit(logger, "table_create", resource_key, dedup_id)
            # Return cached response (return_existing semantics)
            return Success(success_response(cached_response["data"]))

    now = datetime.now(UTC)
    result = create_discussion_table(
        conn,
        question,
        title=title,
        context=context,
        creator_patron_id=creator_patron_id,
        created_by=created_by,
        host_ids=host_ids,
        metadata=metadata,
        policy=policy,
        board=board,
        now=now,
    )
    if isinstance(result, Failure):
        error = result.failure()
        return Failure(error_response("DATABASE_ERROR", f"Failed to create table: {error}"))

    outcome = result.unwrap()
    created = outcome.table

    # Log table creation
    log_table_create(logger, created.id, "mcp:client")

    response_data = {
        "id": created.id,
        "table_id": created.id,
        "question": created.question,
        "title": created.question,
        "context": created.context,
        "status": created.status.value,
        "version": created.version,
        "created_at": created.created_at.isoformat(),
        "updated_at": created.updated_at.isoformat(),
        "creator_patron_id": created.creator_patron_id,
        "creator_id": created.creator_patron_id,
        "created_by": created.creator_patron_id,
        "host_ids": outcome.host_ids,
        "metadata": outcome.metadata,
        "policy": outcome.policy,
        "board": outcome.board,
        "invite_code": outcome.invite_code,
        "web_url": outcome.web_url,
    }
    # Store in idempotency cache if dedup_id provided
    if dedup_id is not None:
        store_idempotency_key(
            conn,
            resource_key,
            "table_create",
            dedup_id,
            {"data": response_data},
            now=now,
        )
    return Success(success_response(response_data))


# Spec defaults for table.join
DEFAULT_HISTORY_LIMIT = 10
DEFAULT_HISTORY_MAX_BYTES = 65536  # 64 KiB


def _create_seat_for_join(
    conn: Any, table_id: str, patron_id: str
) -> Result[dict[str, Any], McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    patron_result = get_patron(conn, PatronId(patron_id))
    if isinstance(patron_result, Failure):
        error = patron_result.failure()
        if isinstance(error, PatronNotFoundError):
            return Failure(error_response("NOT_FOUND", f"Patron not found: {patron_id}"))
        return Failure(error_response("DATABASE_ERROR", f"Failed to get patron: {error}"))

    now = datetime.now(UTC)
    seat_id = SeatId(str(uuid.uuid4()))

    seat = Seat(
        id=seat_id,
        table_id=table_id,
        patron_id=patron_id,
        state=SeatState.JOINED,
        last_heartbeat=now,
        joined_at=now,
    )

    seat_result = create_seat(conn, seat)
    if isinstance(seat_result, Failure):
        error = seat_result.failure()
        return Failure(error_response("DATABASE_ERROR", f"Failed to create seat: {error}"))

    created_seat = seat_result.unwrap()
    expires_at = calculate_expiry_time(created_seat.last_heartbeat, DEFAULT_SEAT_TTL_SECONDS)
    return Success(_build_seat_dict(created_seat, expires_at))


# @shell_complexity: table lookup + can_join guard + seat creation + history fetch + error paths
def table_join(
    table_id: str | None = None,
    patron_id: str | None = None,
    invite_code: str | None = None,
    history_limit: int | None = DEFAULT_HISTORY_LIMIT,
    history_max_bytes: int | None = DEFAULT_HISTORY_MAX_BYTES,
) -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())

    # Resolve table identifier: prefer invite_code, fall back to table_id
    resolved_table_id = invite_code or table_id
    if resolved_table_id is None:
        return Failure(error_response(
            "INVALID_REQUEST",
            "Either invite_code or table_id must be provided",
        ))

    # Verify table exists
    table_result = get_table(conn, TableId(resolved_table_id))
    if isinstance(table_result, Failure):
        error = table_result.failure()
        if isinstance(error, TableNotFoundError):
            return Failure(error_response("NOT_FOUND", f"Table not found: {resolved_table_id}"))
        return Failure(error_response("DATABASE_ERROR", f"Failed to get table: {error}"))

    table = table_result.unwrap()

    # Check state machine guard: only OPEN tables can be joined
    if not can_join(table.status):
        return Failure(error_response(
            "OPERATION_NOT_ALLOWED",
            f"Cannot join table with status '{table.status.value}'. Only OPEN tables accept new joins.",
            {"table_status": table.status.value},
        ))

    # Get max sequence for sequence_latest
    max_seq_result = get_table_max_sequence(conn, resolved_table_id)
    if isinstance(max_seq_result, Failure):
        error = max_seq_result.failure()
        return Failure(error_response("DATABASE_ERROR", f"Failed to get table sequence: {error}"))
    sequence_latest = max_seq_result.unwrap()

    # Get initial history window (apply defaults if agent passed null)
    effective_limit = history_limit if history_limit is not None else DEFAULT_HISTORY_LIMIT
    effective_max_bytes = (
        history_max_bytes if history_max_bytes is not None else DEFAULT_HISTORY_MAX_BYTES
    )
    history_result = get_recent_sayings(
        conn, resolved_table_id, limit=effective_limit, max_bytes=effective_max_bytes
    )
    if isinstance(history_result, Failure):
        error = history_result.failure()
        return Failure(error_response("DATABASE_ERROR", f"Failed to get history: {error}"))

    history_sayings, history_sequence, has_more_history = history_result.unwrap()

    # Compute next_sequence from history. Public table_join uses 0 as the empty-history
    # baseline documented by the MCP spec, while populated history uses the last seen
    # saying sequence so clients can pass it back as since_sequence without duplicates.
    if history_sayings:
        next_sequence = max(s.sequence for s in history_sayings)
        public_history_sequence = history_sequence
    else:
        next_sequence = 0
        public_history_sequence = 0

    # Create seat if patron_id provided (optional - allows human join without seat)
    seat_data = None
    if patron_id is not None:
        seat_result = _create_seat_for_join(conn, resolved_table_id, patron_id)
        if isinstance(seat_result, Failure):
            return seat_result
        seat_data = seat_result.unwrap()

    return Success(success_response(
        {
            "table": _build_table_dict(table),
            "sequence_latest": max(0, sequence_latest),
            "history_sequence": public_history_sequence,
            "initial": {
                "sayings": [_format_saying_dict(s) for s in history_sayings],
                "next_sequence": next_sequence,
                "has_more_history": has_more_history,
            },
            **({"seat": seat_data} if seat_data is not None else {}),
            "_next_action": _build_join_next_action(bool(history_sayings), next_sequence),
        }
    ))


def table_get(table_id: str) -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())
    result = get_table(conn, TableId(table_id))

    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, TableNotFoundError):
            return Failure(error_response("NOT_FOUND", f"Table not found: {table_id}"))
        return Failure(error_response("DATABASE_ERROR", f"Failed to get table: {error}"))

    table = result.unwrap()
    return Success(success_response({"table": _build_table_dict(table)}))


# Valid status filters for table_list
VALID_TABLE_STATUS_FILTERS = ("open", "closed", "paused", "all")


# @shell_complexity: 5 branches for status validation + open-with-seats vs filtered-list dispatch + error paths
def table_list(status: Literal["open", "closed", "paused", "all"] = "open") -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    if status not in VALID_TABLE_STATUS_FILTERS:
        return Failure(error_response(
            "INVALID_REQUEST",
            f"Invalid status filter: '{status}'. Supported values: {', '.join(VALID_TABLE_STATUS_FILTERS)}.",
            {"status": status, "supported": list(VALID_TABLE_STATUS_FILTERS)},
        ))

    conn = next(get_mcp_db())

    # For 'open' status, use the optimized query with seat counts
    if status == "open":
        now = datetime.now(UTC)
        ttl = DEFAULT_SEAT_TTL_SECONDS
        result = list_tables_with_seat_counts(conn, ttl, now)

        if isinstance(result, Failure):
            error = result.failure()
            return Failure(error_response("DATABASE_ERROR", f"Failed to list tables: {error}"))

        tables = result.unwrap()
        return Success(success_response({"tables": tables, "total": len(tables)}))

    # For other statuses, use list_tables and filter
    table_result = list_tables(conn)

    if isinstance(table_result, Failure):
        error = table_result.failure()
        return Failure(error_response("DATABASE_ERROR", f"Failed to list tables: {error}"))

    all_tables = table_result.unwrap()

    if status == "all":
        filtered = all_tables
    else:
        filtered = [t for t in all_tables if t.status.value == status]

    tables_data = [
        {
            "id": t.id,
            "question": t.question,
            "context": t.context,
            "status": t.status.value,
            "version": t.version,
            "created_at": t.created_at.isoformat(),
            "updated_at": t.updated_at.isoformat(),
        }
        for t in filtered
    ]

    return Success(success_response({"tables": tables_data, "total": len(tables_data)}))


# @shell_complexity: 5 branches for input validation + per-ID fetch loop + validation gate + delete result + error paths
def table_delete_batch(ids: list[str]) -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())
    result = delete_tables_batch(conn, ids)
    if isinstance(result, Failure):
        failure = result.failure()
        if failure.status == "invalid_request":
            return Failure(error_response(
                "INVALID_REQUEST",
                failure.error or f"ids must contain 1 to {failure.max_batch_size} table IDs.",
                {"count": len(ids), "max": failure.max_batch_size},
            ))
        if failure.status == "precondition_failed":
            return Failure(error_response(
                "BATCH_PRECONDITION_FAILED",
                "One or more tables cannot be deleted.",
                {
                    "details": [
                        {"id": r.table_id, "reason": r.reason} for r in failure.rejections
                    ],
                },
            ))
        return Failure(error_response(
            "DATABASE_ERROR",
            failure.error or "Failed to batch delete tables.",
        ))

    deleted_ids = result.unwrap().deleted_ids

    log_batch_table_delete(logger, deleted_ids, "mcp")

    return Success(success_response({"deleted_count": len(deleted_ids), "failed": [], "deleted_ids": deleted_ids}))


# Valid export formats
VALID_EXPORT_FORMATS = ("markdown", "jsonl")


# @shell_complexity: 6 branches for format validation + table lookup + sayings fetch + format dispatch + error paths
def table_export(
    table_id: str,
    format: str = "markdown",
) -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())
    result = export_table(conn, table_id, format, exported_at=datetime.now(UTC).isoformat())
    if isinstance(result, Failure):
        error = result.failure()
        if error.status == "invalid_format":
            return Failure(error_response(
                "INVALID_REQUEST",
                error.error or f"Unknown format: {format}. Supported formats: markdown, jsonl",
                {"format": format, "supported": list(VALID_EXPORT_FORMATS)},
            ))
        if error.status == "not_found":
            return Failure(error_response("NOT_FOUND", error.error or f"Table not found: {table_id}"))
        if error.status == "limit_exceeded":
            return Failure(error_response("LIMIT_EXCEEDED", error.error or "Export size exceeded", {"table_id": table_id}))
        return Failure(error_response("DATABASE_ERROR", error.error or "Failed to export table"))

    export_result = result.unwrap()

    return Success(success_response(
        {
            "content": export_result.content,
            "format": format,
            "table_id": table_id,
        }
    ))


def _auto_register_patron_for_say(conn: Any, speaker_name: str | None) -> Result[str, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    auto_name = speaker_name or "Anonymous Agent"
    existing_result = find_patron_by_name(conn, auto_name)
    if isinstance(existing_result, Success):
        existing_patron = existing_result.unwrap()
        if existing_patron is not None:
            return Success(str(existing_patron.id))
    # Create new patron
    new_id = PatronId(str(uuid.uuid4()))
    now = datetime.now(UTC)
    patron = Patron(
        id=new_id,
        name=auto_name,
        kind="agent",
        alias=None,
        meta=None,
        created_at=now,
    )
    create_result = create_patron(conn, patron)
    if isinstance(create_result, Success):
        created = create_result.unwrap()
        return Success(str(created.id))
    return Success(str(new_id))  # Use the generated ID even if store failed


# @shell_complexity: Centralized adapter maps each shared table_say failure family to public MCP codes.
def _table_say_error_to_mcp_response(error: TableSayError) -> Result[McpEnvelope, McpEnvelope]:
    """Map shared table_say errors to the legacy MCP response envelope."""
    if error.kind == TableSayErrorKind.TABLE_NOT_FOUND:
        return Failure(error_response("NOT_FOUND", error.message))
    if error.kind == TableSayErrorKind.OPERATION_NOT_ALLOWED:
        return Failure(error_response(
            "OPERATION_NOT_ALLOWED",
            error.message,
            {"table_status": error.table_status},
        ))
    if error.kind == TableSayErrorKind.INVALID_SPEAKER:
        speaker_details: dict[str, Any] = {"speaker_kind": error.speaker_kind}
        if error.patron_id is not None:
            speaker_details["patron_id"] = error.patron_id
        return Failure(error_response("INVALID_REQUEST", error.message, speaker_details))
    if error.kind == TableSayErrorKind.PATRON_NOT_FOUND:
        return Failure(error_response("NOT_FOUND", error.message))
    if error.kind == TableSayErrorKind.LIMIT_EXCEEDED and error.limit_error is not None:
        return Failure(_limit_error_to_response(error.limit_error))
    if error.kind == TableSayErrorKind.VALIDATION_FAILED and error.validation_error is not None:
        validation = error.validation_error
        details = {
            "kind": validation.kind.value,
            "attachment_index": validation.attachment_index,
            "limit": validation.limit,
            "actual": validation.actual,
        }
        return Failure(error_response(
            "INVALID_REQUEST",
            error.message,
            {key: value for key, value in details.items() if value is not None},
        ))
    return Failure(error_response("DATABASE_ERROR", error.message))


def _resolve_mentions_for_say(
    conn: Any, mentions: list[str] | None
) -> Result[tuple[bool, list[str], list[str]], McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    mentions_all = False
    mentions_resolved: list[str] = []
    mentions_unresolved: list[str] = []

    if not mentions:
        return Success((mentions_all, mentions_resolved, mentions_unresolved))

    patrons_result = list_patrons(conn)
    if isinstance(patrons_result, Failure):
        get_logger(__name__).unwrap().warning(
            "Failed to fetch patrons for mention resolution",
            extra={"error": str(patrons_result.failure())},
        )
        return Success((mentions_all, mentions_resolved, mentions_unresolved))

    patrons = patrons_result.unwrap()
    patron_matches = [
        PatronMatch(patron_id=p.id, alias=p.alias, display_name=p.name) for p in patrons
    ]
    mentions_result = resolve_mentions(mentions, patron_matches)

    if has_ambiguous_mentions(mentions_result):
        return Failure(
            error_response(
                "AMBIGUOUS_MENTION",
                "Multiple patrons match the provided mention handle(s)",
                {
                    "ambiguous": [
                        {
                            "handle": am.handle,
                            "candidates": [c.patron_id for c in am.candidates],
                        }
                        for am in mentions_result.ambiguous
                    ],
                },
            )
        )

    mentions_all = mentions_result.mentions_all
    mentions_resolved = [r.patron_id for r in mentions_result.resolved]
    mentions_unresolved = [u.handle for u in mentions_result.unresolved]
    return Success((mentions_all, mentions_resolved, mentions_unresolved))


def _normalize_attachment_inputs(
    attachments: list[dict[str, str]] | list[AttachmentInput] | None,
) -> Result[list[AttachmentInput], McpEnvelope]:
    """Parse the public attachment object shape before shared validation."""
    if attachments is None:
        return Success([])
    try:
        return Success([
            item if isinstance(item, AttachmentInput) else AttachmentInput.model_validate(item)
            for item in attachments
        ])
    except (TypeError, ValidationError) as exc:
        return Failure(error_response(
            "INVALID_REQUEST",
            "attachments must contain objects with string name and content fields",
            {"validation": str(exc)},
        ))


def _check_say_idempotency(
    conn: Any,
    resource_key: str,
    dedup_id: str | None,
    logger: Any,
    *,
    commit: bool = True,
) -> Result[McpEnvelope | None, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    if dedup_id is None:
        return Success(None)

    idempotency_result = check_idempotency_key(
        conn,
        resource_key,
        "table_say",
        dedup_id,
        commit=commit,
    )
    if isinstance(idempotency_result, Failure):
        return Failure(
            error_response(
                "DATABASE_ERROR", f"Failed to check idempotency key: {idempotency_result.failure()}"
            )
        )

    cached_response = idempotency_result.unwrap()
    if cached_response is not None:
        log_dedup_hit(logger, "table_say", resource_key, dedup_id)
        cached_data = cached_response["data"]
        cached_data["_next_action"] = (
            "Already sent (dedup). IMMEDIATELY call tasca.table_wait. "
            "Your response = tool_call, not text."
        )
        return Success(success_response(cached_data))

    return Success(None)


# @shell_orchestration: SQLite's shared MCP connection needs call-wide transaction ownership.
def table_say(
    table_id: str,
    content: str,
    speaker_kind: Literal["agent", "human"] = "agent",
    patron_id: str | None = None,
    speaker_name: str | None = None,
    saying_type: str | None = None,
    mentions: list[str] | None = None,
    reply_to_sequence: int | None = None,
    dedup_id: str | None = None,
    attachments: list[dict[str, str]] | list[AttachmentInput] | None = None,
) -> Result[McpEnvelope, McpEnvelope]:
    """Serialize a complete table_say call on the shared MCP SQLite connection."""
    with _TABLE_SAY_LOCK:
        return _table_say(
            table_id=table_id,
            content=content,
            speaker_kind=speaker_kind,
            patron_id=patron_id,
            speaker_name=speaker_name,
            saying_type=saying_type,
            mentions=mentions,
            reply_to_sequence=reply_to_sequence,
            dedup_id=dedup_id,
            attachments=attachments,
        )


# @shell_complexity: MCP adapter keeps idempotency/mentions/response envelope around shared table_say operation.
def _table_say(
    table_id: str,
    content: str,
    speaker_kind: Literal["agent", "human"] = "agent",
    patron_id: str | None = None,
    speaker_name: str | None = None,
    saying_type: str | None = None,
    mentions: list[str] | None = None,
    reply_to_sequence: int | None = None,
    dedup_id: str | None = None,
    attachments: list[dict[str, str]] | list[AttachmentInput] | None = None,
) -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())
    table_say_compat_metadata = _build_table_say_compat_metadata(saying_type, reply_to_sequence)
    if table_say_compat_metadata:
        logger.debug(
            "Ignoring unsupported optional table_say fields",
            extra=table_say_compat_metadata,
        )

    actual_speaker_kind = speaker_kind
    validation_result = validate_table_say_speaker_constraints(actual_speaker_kind, patron_id)
    validation_error = validation_result.unwrap()
    if validation_error is not None:
        return _table_say_error_to_mcp_response(validation_error)

    speaker_key = patron_id if actual_speaker_kind == "agent" else "human"
    assert speaker_key is not None

    # Resource key for idempotency scope: {table_id, speaker_key}
    resource_key = f"saying:{table_id}:{speaker_key}"

    transaction_active = False
    if dedup_id is not None:
        try:
            conn.execute("BEGIN IMMEDIATE")
            transaction_active = True
        except sqlite3.Error as exc:
            return Failure(error_response(
                "DATABASE_ERROR",
                f"Failed to start idempotent table_say transaction: {exc}",
            ))

        cached_result = _check_say_idempotency(
            conn,
            resource_key,
            dedup_id,
            logger,
            commit=False,
        )
        if isinstance(cached_result, Failure):
            conn.rollback()
            return cached_result
        cached_response = cached_result.unwrap()
        if cached_response is not None:
            try:
                conn.commit()
            except sqlite3.Error as exc:
                conn.rollback()
                return Failure(error_response(
                    "DATABASE_ERROR",
                    f"Failed to finish idempotent table_say transaction: {exc}",
                ))
            return Success(cached_response)

    attachments_result = _normalize_attachment_inputs(attachments)
    if isinstance(attachments_result, Failure):
        if transaction_active:
            conn.rollback()
        return attachments_result

    # Resolve mentions before append so ambiguity cannot persist a saying.
    mentions_result = _resolve_mentions_for_say(conn, mentions)
    if isinstance(mentions_result, Failure):
        if transaction_active:
            conn.rollback()
        return mentions_result
    mentions_all, mentions_resolved, mentions_unresolved = mentions_result.unwrap()

    limits_result = _limits_config_from_settings()
    if isinstance(limits_result, Failure):
        if transaction_active:
            conn.rollback()
        return limits_result

    try:
        result = append_saying_operation(
            conn,
            table_id=table_id,
            content=content,
            speaker_kind=actual_speaker_kind,
            patron_id=patron_id,
            speaker_name=speaker_name,
            limits=limits_result.unwrap(),
            attachments=attachments_result.unwrap(),
            manage_transaction=not transaction_active,
        )
    except Exception:
        if transaction_active:
            conn.rollback()
        raise

    if isinstance(result, Failure):
        if transaction_active:
            conn.rollback()
        return _table_say_error_to_mcp_response(result.failure())

    saying = result.unwrap().saying
    try:
        response_data = _build_say_response(
            saying, mentions_all, mentions_resolved, mentions_unresolved
        )
    except Exception:
        if transaction_active:
            conn.rollback()
        raise

    if dedup_id is not None:
        store_result = store_idempotency_key(
            conn,
            resource_key,
            "table_say",
            dedup_id,
            {"data": response_data},
            commit=False,
        )
        if isinstance(store_result, Failure):
            conn.rollback()
            return Failure(error_response(
                "DATABASE_ERROR",
                f"Failed to store idempotency key: {store_result.failure()}",
            ))
        try:
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            return Failure(error_response(
                "DATABASE_ERROR",
                f"Failed to commit idempotent table_say: {exc}",
            ))

    log_say(
        logger,
        table_id=saying.table_id,
        sequence=saying.sequence,
        speaker_kind=saying.speaker.kind.value,
        speaker_name=saying.speaker.name,
        patron_id=saying.speaker.patron_id,
    )
    return Success(success_response(response_data))


def attachment_get(attachment_ids: list[str]) -> Result[McpEnvelope, McpEnvelope]:
    """Return 1..8 complete attachment bodies by ID in request order."""
    if not 1 <= len(attachment_ids) <= 8 or any(not item.strip() for item in attachment_ids):
        return Failure(error_response(
            "INVALID_REQUEST",
            "attachment_ids must contain 1..8 non-empty IDs",
        ))

    conn = next(get_mcp_db())
    result = get_attachments_by_ids(conn, attachment_ids)
    if isinstance(result, Failure):
        return Failure(error_response("DATABASE_ERROR", str(result.failure())))
    attachments, missing = result.unwrap()
    if missing:
        return Failure(error_response(
            "NOT_FOUND",
            "One or more attachments were not found",
            {"attachment_ids": missing},
        ))
    return Success(success_response({
        "attachments": [attachment.model_dump(mode="json") for attachment in attachments]
    }))


# @shell_complexity: 5 branches for table lookup + long-poll loop + timeout + backoff + error handling
def table_listen(
    table_id: str,
    since_sequence: int = -1,
    limit: int = 50,
) -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())

    # Verify table exists
    table_result = get_table(conn, TableId(table_id))
    if isinstance(table_result, Failure):
        error = table_result.failure()
        if isinstance(error, TableNotFoundError):
            return Failure(error_response("NOT_FOUND", f"Table not found: {table_id}"))
        return Failure(error_response("DATABASE_ERROR", f"Failed to get table: {error}"))

    # List sayings
    # TODO(async-db): sync DB call in async loop — acceptable for v1, consider asyncio DB driver in future
    result = list_sayings_by_table(conn, table_id, since_sequence, limit)

    if isinstance(result, Failure):
        error = result.failure()
        return Failure(error_response("DATABASE_ERROR", f"Failed to list sayings: {error}"))

    sayings = result.unwrap()

    next_sequence = _compute_next_sequence(sayings, since_sequence)

    return Success(success_response(
        {
            "sayings": [_format_saying_dict(s) for s in sayings],
            "next_sequence": next_sequence,
            "_next_action": (
                f"IMMEDIATELY call tasca.table_wait(since_sequence={next_sequence}). "
                "Your response = tool_call, not text."
            ),
        }
    ))


# =============================================================================
# Table Control Tools
# =============================================================================


# Default timeout for wait endpoint (milliseconds)
DEFAULT_WAIT_MS = 10000
# Maximum wait time (milliseconds) - MUST cap per spec
MAX_WAIT_MS = 10000
# Poll interval for checking new sayings (milliseconds)
POLL_INTERVAL_MS = 500


# @shell_orchestration: CONTROL speaker construction is protocol-local shell wiring.
def _create_control_speaker(speaker_name: str, patron_id: str | None) -> Result[Speaker, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    if patron_id is not None:
        return Success(Speaker(
            kind=SpeakerKind.AGENT,
            name=speaker_name,
            patron_id=PatronId(patron_id),
        ))
    return Success(Speaker(
        kind=SpeakerKind.HUMAN,
        name=speaker_name,
        patron_id=None,
    ))


# @shell_complexity: idempotency + shared atomic control dispatch + MCP error/envelope shaping
def table_control(
    table_id: str,
    action: Literal["pause", "resume", "close"],
    speaker_name: str,
    patron_id: str | None = None,
    reason: str | None = None,
    dedup_id: str | None = None,
) -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())
    auth_error = _authorize_table_mutation(conn, table_id, patron_id)
    if isinstance(auth_error, Failure):
        return Failure(auth_error.failure())

    # Resource key for idempotency scope: {table_id, action}
    resource_key = f"control:{table_id}"

    # Check idempotency key if provided
    if dedup_id is not None:
        idempotency_result = check_idempotency_key(conn, resource_key, "table_control", dedup_id)
        if isinstance(idempotency_result, Failure):
            error = idempotency_result.failure()
            return Failure(error_response("DATABASE_ERROR", f"Failed to check idempotency key: {error}"))

        cached_response = idempotency_result.unwrap()
        if cached_response is not None:
            log_dedup_hit(logger, "table_control", resource_key, dedup_id)
            return Success(success_response(cached_response["data"]))

    speaker_result = _create_control_speaker(speaker_name, patron_id)
    if isinstance(speaker_result, Failure):
        return speaker_result
    speaker = speaker_result.unwrap()
    now = datetime.now(UTC)
    control_result = execute_table_control(conn, table_id, action, speaker, reason, now)
    if isinstance(control_result, Failure):
        error = control_result.failure()
        if error.code == TableControlErrorCode.TABLE_NOT_FOUND:
            return Failure(error_response("NOT_FOUND", error.message))
        if error.code == TableControlErrorCode.VERSION_CONFLICT:
            return Failure(error_response(
                "VERSION_CONFLICT",
                error.message,
                {"expected_version": error.expected_version, "actual_version": error.actual_version},
            ))
        if error.code == TableControlErrorCode.INVALID_ACTION:
            return Failure(error_response("INVALID_ACTION", error.message))
        if error.code == TableControlErrorCode.INVALID_TRANSITION:
            return Failure(error_response("OPERATION_NOT_ALLOWED", error.message, {"table_status": error.current_status.value if error.current_status else None}))
        return Failure(error_response("DATABASE_ERROR", error.message))

    outcome = control_result.unwrap()
    response_data = _build_control_response(outcome.table.status, outcome.control_saying.sequence)

    # Store in idempotency cache if dedup_id provided
    if dedup_id is not None:
        store_idempotency_key(
            conn,
            resource_key,
            "table_control",
            dedup_id,
            {"data": response_data},
            now=now,
        )

    return Success(success_response(response_data))


# @shell_complexity: 8 branches for table lookup + version check + update + dedup + error paths
def table_update(
    table_id: str,
    expected_version: int,
    patch: dict[str, Any],
    speaker_name: str,
    patron_id: str | None = None,
    dedup_id: str | None = None,
) -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())
    table_update_actor_metadata = _build_table_update_actor_metadata(speaker_name, patron_id)
    auth_error = _authorize_table_mutation(conn, table_id, patron_id)
    if isinstance(auth_error, Failure):
        return Failure(auth_error.failure())

    # Resource key for idempotency scope
    resource_key = f"update:{table_id}"

    # Check idempotency key if provided
    if dedup_id is not None:
        idempotency_result = check_idempotency_key(conn, resource_key, "table_update", dedup_id)
        if isinstance(idempotency_result, Failure):
            error = idempotency_result.failure()
            return Failure(error_response("DATABASE_ERROR", f"Failed to check idempotency key: {error}"))

        cached_response = idempotency_result.unwrap()
        if cached_response is not None:
            log_dedup_hit(logger, "table_update", resource_key, dedup_id)
            return Success(success_response(cached_response["data"]))

    # Get current table
    table_result = get_table(conn, TableId(table_id))
    if isinstance(table_result, Failure):
        error = table_result.failure()
        if isinstance(error, TableNotFoundError):
            return Failure(error_response("NOT_FOUND", f"Table not found: {table_id}"))
        return Failure(error_response("DATABASE_ERROR", f"Failed to get table: {error}"))

    current_table = table_result.unwrap()

    # Apply patch to create update (only supported fields)
    table_update, patch_error = _apply_table_patch(current_table, patch)
    if patch_error is not None:
        return Failure(patch_error)

    # Perform optimistic concurrency update
    now = datetime.now(UTC)
    update_result = update_table(
        conn=conn,
        table_id=TableId(table_id),
        update=table_update,
        expected_version=Version(expected_version),
        now=now,
    )

    if isinstance(update_result, Failure):
        error = update_result.failure()
        if isinstance(error, TableNotFoundError):
            return Failure(error_response("NOT_FOUND", f"Table not found: {table_id}"))
        if isinstance(error, VersionConflictError):
            return Failure(error_response(
                "VERSION_CONFLICT",
                "Table version conflict",
                {
                    "expected_version": error.expected_version,
                    "actual_version": error.current_version,
                    "table": _build_table_dict(current_table),
                },
            ))
        return Failure(error_response("DATABASE_ERROR", f"Failed to update table: {error}"))

    updated_table = update_result.unwrap()
    response_data = {"table": _build_table_dict(updated_table)}
    logger.debug("table_update actor metadata", extra=table_update_actor_metadata)

    # Store in idempotency cache if dedup_id provided
    if dedup_id is not None:
        store_idempotency_key(
            conn,
            resource_key,
            "table_update",
            dedup_id,
            {"data": response_data},
            now=now,
        )

    return Success(success_response(response_data))


# @shell_complexity: 10 branches for table lookup + long-poll loop + timeout + backoff + error handling
# Note: FastMCP supports async tools - using async def for blocking wait
async def table_wait(
    table_id: str,
    since_sequence: int = -1,
    wait_ms: int = DEFAULT_WAIT_MS,
    limit: int = 50,
    include_table: bool = False,
) -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())

    # Verify table exists
    table_result = get_table(conn, TableId(table_id))
    if isinstance(table_result, Failure):
        error = table_result.failure()
        if isinstance(error, TableNotFoundError):
            return Failure(error_response("NOT_FOUND", f"Table not found: {table_id}"))
        return Failure(error_response("DATABASE_ERROR", f"Failed to get table: {error}"))

    table = table_result.unwrap()

    # Cap wait_ms at MAX_WAIT_MS per spec
    capped_wait_ms = min(wait_ms, MAX_WAIT_MS)
    timeout_seconds = capped_wait_ms / 1000.0
    poll_interval_seconds = POLL_INTERVAL_MS / 1000.0

    start_time = time.monotonic()
    end_time = start_time + timeout_seconds

    while time.monotonic() < end_time:
        # Check for new sayings
        # TODO(async-db): sync DB call in async loop — acceptable for v1, consider asyncio DB driver in future
        result = list_sayings_by_table(conn, table_id, since_sequence, limit=limit)

        if isinstance(result, Failure):
            error = result.failure()
            return Failure(error_response("DATABASE_ERROR", f"Failed to list sayings: {error}"))

        sayings = result.unwrap()

        if sayings:
            # Found new sayings - return them
            next_sequence = _compute_next_sequence(sayings, since_sequence)
            loop_result = _record_wait_result(table_id, got_sayings=True)
            loop = loop_result.unwrap()

            response_data: dict[str, Any] = {
                "sayings": [_format_saying_dict(s) for s in sayings],
                "next_sequence": next_sequence,
                "timeout": False,
                "_loop_state": loop,
            }

            if include_table:
                response_data["table"] = _build_table_dict(table)

            response_data["_next_action"] = (
                "New sayings received. Decide internally whether to speak. "
                "Your next response MUST be a tool_call: either "
                f"tasca.table_say(...) or tasca.table_wait(since_sequence={next_sequence}). "
                "Do not emit text."
            )

            return Success(success_response(response_data))

        # Wait before next poll
        remaining = end_time - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(poll_interval_seconds, remaining))

    # Timeout - return empty with current next_sequence (same shape as table_listen)
    next_sequence = _compute_next_sequence([], since_sequence)
    loop_result = _record_wait_result(table_id, got_sayings=False)
    loop = loop_result.unwrap()
    empty = loop["consecutive_empty_waits"]

    response_data = {
        "sayings": [],
        "next_sequence": next_sequence,
        "timeout": True,
        "_loop_state": loop,
        "_next_action": _silence_next_action(empty, next_sequence),
    }

    if include_table:
        response_data["table"] = _build_table_dict(table)

    return Success(success_response(response_data))


# =============================================================================
# Seat Tools
# =============================================================================


def seat_heartbeat(
    table_id: str,
    patron_id: str | None = None,
    state: Literal["running", "idle", "done"] | None = None,
    ttl_ms: int | None = None,
    dedup_id: str | None = None,
    seat_id: str | None = None,
) -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    return _seat_heartbeat_impl(table_id, patron_id, state, ttl_ms, dedup_id, seat_id)


def seat_list(
    table_id: str,
    active_only: bool = True,
) -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    return _seat_list_impl(table_id, active_only)


# =============================================================================
# Proxy Control Tools
# =============================================================================


async def connect(url: str | None = None, token: str | None = None) -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    return await _connect_impl(url, token)


def connection_status() -> Result[McpEnvelope, McpEnvelope]:
    """Implementation detail for MCP tool behavior."""
    return _connection_status_impl()
