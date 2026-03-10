# @invar:allow file_size: MCP tool orchestration extracted from server entrypoints
"""MCP tool entrypoint implementations extracted from server module."""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from returns.result import Failure, Success

from tasca.core.domain.patron import Patron, PatronId
from tasca.core.domain.saying import Speaker, SpeakerKind
from tasca.core.domain.seat import Seat, SeatId, SeatState
from tasca.core.domain.table import Table, TableId, TableStatus, TableUpdate, Version
from tasca.core.export_service import generate_jsonl, generate_markdown
from tasca.core.services.batch_delete_service import MAX_BATCH_SIZE, validate_batch_delete_request
from tasca.core.services.limits_service import LimitError, LimitsConfig, settings_to_limits_config
from tasca.core.services.mention_service import (
    PatronMatch,
    has_ambiguous_mentions,
    resolve_mentions,
)
from tasca.core.services.seat_service import (
    DEFAULT_SEAT_TTL_SECONDS,
    calculate_expiry_time,
    filter_active_seats,
)
from tasca.core.table_state_machine import (
    can_join,
    can_say,
    is_terminal,
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
from tasca.shell.mcp.entrypoint_logic import (
    validate_control_action as _validate_control_action,
)
from tasca.shell.mcp.entrypoint_logic import (
    validate_speaker_constraints as _validate_speaker_constraints,
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
from tasca.shell.services.limited_saying_service import append_saying_with_limits
from tasca.shell.services.table_id_generator import generate_table_id
from tasca.shell.storage.idempotency_repo import check_idempotency_key, store_idempotency_key
from tasca.shell.storage.patron_repo import (
    PatronNotFoundError,
    create_patron,
    find_patron_by_name,
    get_patron,
    list_patrons,
)
from tasca.shell.storage.saying_repo import (
    append_saying,
    get_recent_sayings,
    get_table_max_sequence,
    list_all_sayings_by_table,
    list_sayings_by_table,
)
from tasca.shell.storage.seat_repo import (
    create_seat,
)
from tasca.shell.storage.table_repo import (
    TableNotFoundError,
    VersionConflictError,
    batch_delete_tables,
    create_table,
    get_table,
    list_tables,
    list_tables_with_seat_counts,
    update_table,
)

logger = get_logger(__name__)

# Per-session loop state tracking.
# MCP server runs per-agent (stdio) or per-session (HTTP), so module-level state
# is scoped to a single agent's session. Keyed by table_id.
_loop_state: dict[str, dict[str, int]] = {}


# @invar:allow shell_result: entrypoints.py - pure in-memory state, not I/O
# @shell_orchestration: Session loop state is MCP runtime orchestration state.
def _get_loop_state(table_id: str) -> dict[str, int]:
    """Implementation detail for MCP tool behavior."""
    if table_id not in _loop_state:
        _loop_state[table_id] = {
            "consecutive_empty_waits": 0,
            "total_iterations": 0,
        }
    return _loop_state[table_id]


# @invar:allow shell_result: entrypoints.py - pure in-memory state, not I/O
# @shell_orchestration: Session loop counters must remain in MCP shell state.
def _record_wait_result(table_id: str, *, got_sayings: bool) -> dict[str, int]:
    """Implementation detail for MCP tool behavior."""
    state = _get_loop_state(table_id)
    state["total_iterations"] += 1
    if got_sayings:
        state["consecutive_empty_waits"] = 0
    else:
        state["consecutive_empty_waits"] += 1
    return state


# @invar:allow shell_result: entrypoints.py - MCP settings adapter returns domain config, not Result
# @shell_orchestration: Shell-local adapter from settings to MCP limits behavior.
def _limits_config_from_settings() -> LimitsConfig:
    """Implementation detail for MCP tool behavior."""
    from tasca.config import settings as _settings  # Lazy import for test monkeypatching

    return settings_to_limits_config(_settings)


# =============================================================================
# Patron Tools
# =============================================================================


# @shell_complexity: 10 branches for patron dedup check + create + idempotency store + error paths + backward compat
# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
def patron_register(
    display_name: str | None = None,
    alias: str | None = None,
    meta: dict[str, Any] | None = None,
    patron_id: str | None = None,
    dedup_id: str | None = None,
    # Backward compatibility: accept 'name' as alias for display_name
    name: str | None = None,
    kind: str = "agent",
) -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    # Backward compatibility: fall back to 'name' if display_name not provided
    resolved_name = display_name or name
    if resolved_name is None:
        return error_response(
            "INVALID_REQUEST",
            "display_name (or name for backward compat) is required",
        )

    conn = next(get_mcp_db())

    # Resource key for idempotency scope (patron registration uses name as scope)
    resource_key = f"patron:{resolved_name}"

    # Check idempotency key if provided
    if dedup_id is not None:
        idempotency_result = check_idempotency_key(conn, resource_key, "patron_register", dedup_id)
        if isinstance(idempotency_result, Failure):
            error = idempotency_result.failure()
            return error_response("DATABASE_ERROR", f"Failed to check idempotency key: {error}")

        cached_response = idempotency_result.unwrap()
        if cached_response is not None:
            # Log dedup hit
            log_dedup_hit(logger, "patron_register", resource_key, dedup_id)
            # Return cached response (return_existing semantics)
            return success_response(cached_response["data"])

    # Check for existing patron by name (dedup)
    existing_result = find_patron_by_name(conn, resolved_name)

    if isinstance(existing_result, Failure):
        error = existing_result.failure()
        return error_response("DATABASE_ERROR", f"Failed to check for existing patron: {error}")

    existing = existing_result.unwrap()
    if existing is not None:
        # Return existing patron (return_existing semantics)
        response_data = _build_patron_response_data(existing, is_new=False)
        # Store in idempotency cache if dedup_id provided
        if dedup_id is not None:
            store_idempotency_key(
                conn, resource_key, "patron_register", dedup_id, {"data": response_data}
            )
        return success_response(response_data)

    # Create new patron
    now = datetime.now(UTC)
    new_patron_id = PatronId(patron_id) if patron_id else PatronId(str(uuid.uuid4()))

    patron = Patron(
        id=new_patron_id,
        name=resolved_name,
        kind=kind,
        alias=alias,
        meta=meta,
        created_at=now,
    )

    result = create_patron(conn, patron)

    if isinstance(result, Failure):
        error = result.failure()
        return error_response("DATABASE_ERROR", f"Failed to create patron: {error}")

    created = result.unwrap()
    response_data = _build_patron_response_data(created, is_new=True)
    # Store in idempotency cache if dedup_id provided
    if dedup_id is not None:
        store_idempotency_key(
            conn,
            resource_key,
            "patron_register",
            dedup_id,
            {"data": response_data},
            now=now,
        )
    return success_response(response_data)


# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
def patron_get(patron_id: str) -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())
    result = get_patron(conn, PatronId(patron_id))

    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, PatronNotFoundError):
            return error_response("NOT_FOUND", f"Patron not found: {patron_id}")
        return error_response("DATABASE_ERROR", f"Failed to get patron: {error}")

    patron = result.unwrap()
    return success_response(
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
    )


# =============================================================================
# Table Tools
# =============================================================================


# @shell_complexity: 5 branches for table creation + dedup store + idempotency + error handling
# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
def table_create(
    question: str,
    context: str | None = None,
    creator_patron_id: str | None = None,
    dedup_id: str | None = None,
) -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())

    # Resource key for idempotency scope (table_create uses global scope via dedup_id)
    resource_key = "table_create"

    # Check idempotency key if provided
    if dedup_id is not None:
        idempotency_result = check_idempotency_key(conn, resource_key, "table_create", dedup_id)
        if isinstance(idempotency_result, Failure):
            error = idempotency_result.failure()
            return error_response("DATABASE_ERROR", f"Failed to check idempotency key: {error}")

        cached_response = idempotency_result.unwrap()
        if cached_response is not None:
            # Log dedup hit
            log_dedup_hit(logger, "table_create", resource_key, dedup_id)
            # Return cached response (return_existing semantics)
            return success_response(cached_response["data"])

    now = datetime.now(UTC)
    table_id_result = generate_table_id(conn)

    if isinstance(table_id_result, Failure):
        error = table_id_result.failure()
        return error_response("DATABASE_ERROR", f"Failed to generate table ID: {error}")

    table_id = table_id_result.unwrap()

    table = Table(
        id=table_id,
        question=question,
        context=context,
        status=TableStatus.OPEN,
        version=Version(1),
        created_at=now,
        updated_at=now,
        creator_patron_id=creator_patron_id,
    )

    result = create_table(conn, table)

    if isinstance(result, Failure):
        error = result.failure()
        return error_response("DATABASE_ERROR", f"Failed to create table: {error}")

    created = result.unwrap()

    # Log table creation
    log_table_create(logger, created.id, "mcp:client")

    response_data = {
        "id": created.id,
        "question": created.question,
        "context": created.context,
        "status": created.status.value,
        "version": created.version,
        "created_at": created.created_at.isoformat(),
        "updated_at": created.updated_at.isoformat(),
        "creator_patron_id": created.creator_patron_id,
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
    return success_response(response_data)


# Spec defaults for table.join
DEFAULT_HISTORY_LIMIT = 10
DEFAULT_HISTORY_MAX_BYTES = 65536  # 64 KiB


# @invar:allow shell_result: entrypoints.py - MCP helper returns dict responses, not Result
def _create_seat_for_join(
    conn: Any, table_id: str, patron_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Implementation detail for MCP tool behavior."""
    patron_result = get_patron(conn, PatronId(patron_id))
    if isinstance(patron_result, Failure):
        error = patron_result.failure()
        if isinstance(error, PatronNotFoundError):
            return None, error_response("NOT_FOUND", f"Patron not found: {patron_id}")
        return None, error_response("DATABASE_ERROR", f"Failed to get patron: {error}")

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
        return None, error_response("DATABASE_ERROR", f"Failed to create seat: {error}")

    created_seat = seat_result.unwrap()
    expires_at = calculate_expiry_time(created_seat.last_heartbeat, DEFAULT_SEAT_TTL_SECONDS)
    return _build_seat_dict(created_seat, expires_at), None


# @shell_complexity: table lookup + can_join guard + seat creation + history fetch + error paths
# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
def table_join(
    table_id: str | None = None,
    patron_id: str | None = None,
    invite_code: str | None = None,
    history_limit: int | None = DEFAULT_HISTORY_LIMIT,
    history_max_bytes: int | None = DEFAULT_HISTORY_MAX_BYTES,
) -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())

    # Resolve table identifier: prefer invite_code, fall back to table_id
    resolved_table_id = invite_code or table_id
    if resolved_table_id is None:
        return error_response(
            "INVALID_REQUEST",
            "Either invite_code or table_id must be provided",
        )

    # Verify table exists
    table_result = get_table(conn, TableId(resolved_table_id))
    if isinstance(table_result, Failure):
        error = table_result.failure()
        if isinstance(error, TableNotFoundError):
            return error_response("NOT_FOUND", f"Table not found: {resolved_table_id}")
        return error_response("DATABASE_ERROR", f"Failed to get table: {error}")

    table = table_result.unwrap()

    # Check state machine guard: only OPEN tables can be joined
    if not can_join(table.status):
        return error_response(
            "OPERATION_NOT_ALLOWED",
            f"Cannot join table with status '{table.status.value}'. Only OPEN tables accept new joins.",
            {"table_status": table.status.value},
        )

    # Get max sequence for sequence_latest
    max_seq_result = get_table_max_sequence(conn, resolved_table_id)
    if isinstance(max_seq_result, Failure):
        error = max_seq_result.failure()
        return error_response("DATABASE_ERROR", f"Failed to get table sequence: {error}")
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
        return error_response("DATABASE_ERROR", f"Failed to get history: {error}")

    history_sayings, history_sequence, has_more_history = history_result.unwrap()

    # Compute next_sequence from history.
    # Convention: -1 means "no sayings yet" — client uses since_sequence=-1 to fetch all.
    # Note: table_listen/table_wait use a different convention via _compute_next_sequence
    # (empty → since_sequence+1, not -1) because they only return new sayings, not history.
    if history_sayings:
        next_sequence = max(s.sequence for s in history_sayings)
    else:
        next_sequence = -1

    # Create seat if patron_id provided (optional - allows human join without seat)
    seat_data = None
    if patron_id is not None:
        seat_data, seat_error = _create_seat_for_join(conn, resolved_table_id, patron_id)
        if seat_error is not None:
            return seat_error

    return success_response(
        {
            "table": _build_table_dict(table),
            "sequence_latest": sequence_latest,
            "history_sequence": history_sequence,
            "initial_sayings": {
                "sayings": [_format_saying_dict(s) for s in history_sayings],
                "next_sequence": next_sequence,
                "has_more": has_more_history,
            },
            **({"seat": seat_data} if seat_data is not None else {}),
            "_next_action": _build_join_next_action(bool(history_sayings), next_sequence),
        }
    )


# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
def table_get(table_id: str) -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())
    result = get_table(conn, TableId(table_id))

    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, TableNotFoundError):
            return error_response("NOT_FOUND", f"Table not found: {table_id}")
        return error_response("DATABASE_ERROR", f"Failed to get table: {error}")

    table = result.unwrap()
    return success_response(
        {
            "id": table.id,
            "question": table.question,
            "context": table.context,
            "status": table.status.value,
            "version": table.version,
            "created_at": table.created_at.isoformat(),
            "updated_at": table.updated_at.isoformat(),
        }
    )


# Valid status filters for table_list
VALID_TABLE_STATUS_FILTERS = ("open", "closed", "paused", "all")


# @shell_complexity: 5 branches for status validation + open-with-seats vs filtered-list dispatch + error paths
# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
def table_list(status: Literal["open", "closed", "paused", "all"] = "open") -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    if status not in VALID_TABLE_STATUS_FILTERS:
        return error_response(
            "INVALID_REQUEST",
            f"Invalid status filter: '{status}'. Supported values: {', '.join(VALID_TABLE_STATUS_FILTERS)}.",
            {"status": status, "supported": list(VALID_TABLE_STATUS_FILTERS)},
        )

    conn = next(get_mcp_db())

    # For 'open' status, use the optimized query with seat counts
    if status == "open":
        now = datetime.now(UTC)
        ttl = DEFAULT_SEAT_TTL_SECONDS
        result = list_tables_with_seat_counts(conn, ttl, now)

        if isinstance(result, Failure):
            error = result.failure()
            return error_response("DATABASE_ERROR", f"Failed to list tables: {error}")

        tables = result.unwrap()
        return success_response({"tables": tables, "total": len(tables)})

    # For other statuses, use list_tables and filter
    result = list_tables(conn)

    if isinstance(result, Failure):
        error = result.failure()
        return error_response("DATABASE_ERROR", f"Failed to list tables: {error}")

    all_tables = result.unwrap()

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

    return success_response({"tables": tables_data, "total": len(tables_data)})


# @shell_complexity: 5 branches for input validation + per-ID fetch loop + validation gate + delete result + error paths
# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
def table_delete_batch(ids: list[str]) -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    if not ids or len(ids) > MAX_BATCH_SIZE:
        return error_response(
            "INVALID_REQUEST",
            f"ids must contain 1 to {MAX_BATCH_SIZE} table IDs.",
            {"count": len(ids), "max": MAX_BATCH_SIZE},
        )

    conn = next(get_mcp_db())

    # Fetch all requested tables for validation
    tables_for_validation = []
    for tid in ids:
        result = get_table(conn, TableId(tid))
        if isinstance(result, Success):
            tables_for_validation.append(result.unwrap())

    # Validate: all must exist and be closed
    validation = validate_batch_delete_request(tables_for_validation, ids)

    if not validation.is_valid:
        return error_response(
            "BATCH_PRECONDITION_FAILED",
            "One or more tables cannot be deleted.",
            {
                "details": [{"id": r.table_id, "reason": r.reason} for r in validation.rejections],
            },
        )

    # Execute cascade delete
    delete_result = batch_delete_tables(conn, validation.valid_ids)

    if isinstance(delete_result, Failure):
        error = delete_result.failure()
        return error_response("DATABASE_ERROR", f"Failed to batch delete tables: {error}")

    deleted_ids = delete_result.unwrap()

    log_batch_table_delete(logger, deleted_ids, "mcp")

    return success_response({"deleted_ids": deleted_ids})


# Valid export formats
VALID_EXPORT_FORMATS = ("markdown", "jsonl")


# @shell_complexity: 6 branches for format validation + table lookup + sayings fetch + format dispatch + error paths
# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
def table_export(
    table_id: str,
    format: str = "markdown",
) -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    # Validate format early - return INVALID_REQUEST envelope instead of raising ValidationError
    if format not in VALID_EXPORT_FORMATS:
        return error_response(
            "INVALID_REQUEST",
            f"Unknown format: {format}. Supported formats: markdown, jsonl",
            {"format": format, "supported": list(VALID_EXPORT_FORMATS)},
        )

    conn = next(get_mcp_db())

    # Verify table exists and fetch it
    table_result = get_table(conn, TableId(table_id))
    if isinstance(table_result, Failure):
        error = table_result.failure()
        if isinstance(error, TableNotFoundError):
            return error_response("NOT_FOUND", f"Table not found: {table_id}")
        return error_response("DATABASE_ERROR", f"Failed to get table: {error}")

    table = table_result.unwrap()

    # Fetch ALL sayings for export (no count truncation)
    sayings_result = list_all_sayings_by_table(conn, table_id)
    if isinstance(sayings_result, Failure):
        error_msg = str(sayings_result.failure())
        # Check if it's a size exceeded error
        if "Export size exceeded" in error_msg:
            return error_response(
                "LIMIT_EXCEEDED",
                error_msg,
                {"table_id": table_id},
            )
        return error_response("DATABASE_ERROR", f"Failed to list sayings: {error_msg}")

    sayings = sayings_result.unwrap()

    # Generate export content based on format (format already validated above)
    if format == "jsonl":
        exported_at = datetime.now(UTC).isoformat()
        content = generate_jsonl(table, sayings, exported_at)
    else:  # markdown (default, already validated)
        content = generate_markdown(table, sayings)

    return success_response(
        {
            "content": content,
            "format": format,
            "table_id": table_id,
        }
    )


# @invar:allow shell_result: entrypoints.py - MCP helper performs I/O, returns patron_id or None
def _auto_register_patron_for_say(conn: Any, speaker_name: str | None) -> str | None:
    """Implementation detail for MCP tool behavior."""
    auto_name = speaker_name or "Anonymous Agent"
    existing_result = find_patron_by_name(conn, auto_name)
    if isinstance(existing_result, Success):
        existing_patron = existing_result.unwrap()
        if existing_patron is not None:
            return existing_patron.id
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
        return created.id
    return new_id  # Use the generated ID even if store failed


# @invar:allow shell_result: entrypoints.py - MCP helper returns dict responses, not Result
# @shell_complexity: Resolves optional identity across DB lookup and speaker-kind branches.
def _resolve_speaker_for_say(
    conn: Any,
    actual_speaker_kind: str,
    patron_id: str | None,
    speaker_name: str | None,
) -> tuple[Speaker, str, dict[str, Any] | None]:
    """Implementation detail for MCP tool behavior."""
    resolved_name = speaker_name
    if resolved_name is None:
        if patron_id is not None:
            patron_result = get_patron(conn, PatronId(patron_id))
            if isinstance(patron_result, Failure):
                error = patron_result.failure()
                if isinstance(error, PatronNotFoundError):
                    return (
                        Speaker(
                            kind=SpeakerKind.AGENT,
                            name="Unknown",
                            patron_id=None,
                        ),
                        "",
                        error_response("NOT_FOUND", f"Patron not found: {patron_id}"),
                    )
                return (
                    Speaker(
                        kind=SpeakerKind.AGENT,
                        name="Unknown",
                        patron_id=None,
                    ),
                    "",
                    error_response("DATABASE_ERROR", f"Failed to get patron: {error}"),
                )
            patron = patron_result.unwrap()
            resolved_name = patron.name
        else:
            resolved_name = "Human"

    if actual_speaker_kind == "agent":
        assert patron_id is not None  # validated earlier
        return (
            Speaker(kind=SpeakerKind.AGENT, name=resolved_name, patron_id=PatronId(patron_id)),
            patron_id,
            None,
        )
    else:
        return (
            Speaker(kind=SpeakerKind.HUMAN, name=resolved_name, patron_id=None),
            "human",
            None,
        )


# @invar:allow shell_result: entrypoints.py - MCP helper returns dict responses, not Result
def _resolve_mentions_for_say(
    conn: Any, mentions: list[str] | None
) -> tuple[bool, list[str], list[str], dict[str, Any] | None]:
    """Implementation detail for MCP tool behavior."""
    mentions_all = False
    mentions_resolved: list[str] = []
    mentions_unresolved: list[str] = []

    if not mentions:
        return mentions_all, mentions_resolved, mentions_unresolved, None

    patrons_result = list_patrons(conn)
    if isinstance(patrons_result, Failure):
        get_logger(__name__).warning(
            "Failed to fetch patrons for mention resolution",
            extra={"error": str(patrons_result.failure())},
        )
        return mentions_all, mentions_resolved, mentions_unresolved, None

    patrons = patrons_result.unwrap()
    patron_matches = [
        PatronMatch(patron_id=p.id, alias=p.alias, display_name=p.name) for p in patrons
    ]
    mentions_result = resolve_mentions(mentions, patron_matches)

    if has_ambiguous_mentions(mentions_result):
        return (
            mentions_all,
            mentions_resolved,
            mentions_unresolved,
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
            ),
        )

    mentions_all = mentions_result.mentions_all
    mentions_resolved = [r.patron_id for r in mentions_result.resolved]
    mentions_unresolved = [u.handle for u in mentions_result.unresolved]
    return mentions_all, mentions_resolved, mentions_unresolved, None


# @invar:allow shell_result: entrypoints.py - MCP helper returns dict responses, not Result
def _check_say_idempotency(
    conn: Any, resource_key: str, dedup_id: str | None, logger: Any
) -> tuple[dict[str, Any] | None, bool]:
    """Implementation detail for MCP tool behavior."""
    if dedup_id is None:
        return None, False

    idempotency_result = check_idempotency_key(conn, resource_key, "table_say", dedup_id)
    if isinstance(idempotency_result, Failure):
        return (
            error_response(
                "DATABASE_ERROR", f"Failed to check idempotency key: {idempotency_result.failure()}"
            ),
            True,
        )

    cached_response = idempotency_result.unwrap()
    if cached_response is not None:
        log_dedup_hit(logger, "table_say", resource_key, dedup_id)
        cached_data = cached_response["data"]
        cached_data["_next_action"] = (
            "Already sent (dedup). IMMEDIATELY call tasca.table_wait. "
            "Your response = tool_call, not text."
        )
        return success_response(cached_data), True

    return None, False


# @shell_complexity: table lookup + can_say guard + limits enforcement + error paths
# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
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
) -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())
    table_say_compat_metadata = _build_table_say_compat_metadata(saying_type, reply_to_sequence)
    if table_say_compat_metadata:
        logger.debug(
            "Ignoring unsupported optional table_say fields",
            extra=table_say_compat_metadata,
        )

    # Auto-register patron if agent calls table_say without patron_id.
    actual_speaker_kind = speaker_kind
    if speaker_kind == "agent" and patron_id is None:
        patron_id = _auto_register_patron_for_say(conn, speaker_name)

    # Validate speaker_kind + patron_id constraints
    validation_error = _validate_speaker_constraints(actual_speaker_kind, patron_id)
    if validation_error is not None:
        return validation_error

    # Verify table exists
    table_result = get_table(conn, TableId(table_id))
    if isinstance(table_result, Failure):
        error = table_result.failure()
        if isinstance(error, TableNotFoundError):
            return error_response("NOT_FOUND", f"Table not found: {table_id}")
        return error_response("DATABASE_ERROR", f"Failed to get table: {error}")

    table = table_result.unwrap()

    # Check state machine guard: CLOSED tables cannot have sayings added
    if not can_say(table.status):
        return error_response(
            "OPERATION_NOT_ALLOWED",
            f"Cannot add saying to table with status '{table.status.value}'. Table must be OPEN or PAUSED.",
            {"table_status": table.status.value},
        )

    # Resolve speaker
    speaker, speaker_key, speaker_error = _resolve_speaker_for_say(
        conn, actual_speaker_kind, patron_id, speaker_name
    )
    if speaker_error is not None:
        return speaker_error

    # Resource key for idempotency scope: {table_id, speaker_key}
    resource_key = f"saying:{table_id}:{speaker_key}"

    # Check idempotency key if provided
    cached_response, should_return = _check_say_idempotency(conn, resource_key, dedup_id, logger)
    if should_return and cached_response is not None:
        return cached_response

    # Get limits configuration and append with limits check
    limits = _limits_config_from_settings()
    result = append_saying_with_limits(conn, table_id, speaker, content, limits)

    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, LimitError):
            return _limit_error_to_response(error)
        return error_response("DATABASE_ERROR", f"Failed to append saying: {error}")

    saying = result.unwrap()

    # Resolve mentions if provided
    mentions_all, mentions_resolved, mentions_unresolved, mentions_error = (
        _resolve_mentions_for_say(conn, mentions)
    )
    if mentions_error is not None:
        return mentions_error

    # Log saying append
    log_say(
        logger,
        table_id=saying.table_id,
        sequence=saying.sequence,
        speaker_kind=saying.speaker.kind.value,
        speaker_name=saying.speaker.name,
        patron_id=saying.speaker.patron_id,
    )

    # Build response
    response_data = _build_say_response(
        saying, mentions_all, mentions_resolved, mentions_unresolved
    )

    # Store in idempotency cache if dedup_id provided
    if dedup_id is not None:
        store_idempotency_key(conn, resource_key, "table_say", dedup_id, {"data": response_data})
    return success_response(response_data)


# @shell_complexity: 5 branches for table lookup + long-poll loop + timeout + backoff + error handling
# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
def table_listen(
    table_id: str,
    since_sequence: int = -1,
    limit: int = 50,
) -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())

    # Verify table exists
    table_result = get_table(conn, TableId(table_id))
    if isinstance(table_result, Failure):
        error = table_result.failure()
        if isinstance(error, TableNotFoundError):
            return error_response("NOT_FOUND", f"Table not found: {table_id}")
        return error_response("DATABASE_ERROR", f"Failed to get table: {error}")

    # List sayings
    # TODO(async-db): sync DB call in async loop — acceptable for v1, consider asyncio DB driver in future
    result = list_sayings_by_table(conn, table_id, since_sequence, limit)

    if isinstance(result, Failure):
        error = result.failure()
        return error_response("DATABASE_ERROR", f"Failed to list sayings: {error}")

    sayings = result.unwrap()

    next_sequence = _compute_next_sequence(sayings, since_sequence)

    return success_response(
        {
            "sayings": [
                {
                    "id": s.id,
                    "table_id": s.table_id,
                    "sequence": s.sequence,
                    "speaker": {
                        "kind": s.speaker.kind.value,
                        "name": s.speaker.name,
                        "patron_id": s.speaker.patron_id,
                    },
                    "content": s.content,
                    "pinned": s.pinned,
                    "created_at": s.created_at.isoformat(),
                }
                for s in sayings
            ],
            "next_sequence": next_sequence,
            "_next_action": (
                f"IMMEDIATELY call tasca.table_wait(since_sequence={next_sequence}). "
                "Your response = tool_call, not text."
            ),
        }
    )


# =============================================================================
# Table Control Tools
# =============================================================================


# Default timeout for wait endpoint (milliseconds)
DEFAULT_WAIT_MS = 10000
# Maximum wait time (milliseconds) - MUST cap per spec
MAX_WAIT_MS = 10000
# Poll interval for checking new sayings (milliseconds)
POLL_INTERVAL_MS = 500


# @invar:allow shell_result: entrypoints.py - MCP helper returns dict responses, not Result
# @shell_orchestration: CONTROL speaker construction is protocol-local shell wiring.
def _create_control_speaker(speaker_name: str, patron_id: str | None) -> Speaker:
    """Implementation detail for MCP tool behavior."""
    if patron_id is not None:
        return Speaker(
            kind=SpeakerKind.AGENT,
            name=speaker_name,
            patron_id=PatronId(patron_id),
        )
    return Speaker(
        kind=SpeakerKind.HUMAN,
        name=speaker_name,
        patron_id=None,
    )


# @invar:allow shell_result: entrypoints.py - MCP helper returns dict responses, not Result
def _append_control_saying(
    conn: Any, table_id: str, action: str, reason: str | None, speaker: Speaker
) -> tuple[Any | None, dict[str, Any] | None]:
    """Implementation detail for MCP tool behavior."""
    control_content = f"**CONTROL: {action.upper()}**"
    if reason:
        control_content += f"\n\nReason: {reason}"

    saying_result = append_saying(conn, table_id, speaker, control_content)
    if isinstance(saying_result, Failure):
        error = saying_result.failure()
        return None, error_response("DATABASE_ERROR", f"Failed to append control saying: {error}")

    return saying_result.unwrap(), None


# @shell_complexity: table lookup + state machine validation + status update + error paths
# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
def table_control(
    table_id: str,
    action: Literal["pause", "resume", "close"],
    speaker_name: str,
    patron_id: str | None = None,
    reason: str | None = None,
    dedup_id: str | None = None,
) -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())

    # Resource key for idempotency scope: {table_id, action}
    resource_key = f"control:{table_id}"

    # Check idempotency key if provided
    if dedup_id is not None:
        idempotency_result = check_idempotency_key(conn, resource_key, "table_control", dedup_id)
        if isinstance(idempotency_result, Failure):
            error = idempotency_result.failure()
            return error_response("DATABASE_ERROR", f"Failed to check idempotency key: {error}")

        cached_response = idempotency_result.unwrap()
        if cached_response is not None:
            log_dedup_hit(logger, "table_control", resource_key, dedup_id)
            return success_response(cached_response["data"])

    # Get current table
    table_result = get_table(conn, TableId(table_id))
    if isinstance(table_result, Failure):
        error = table_result.failure()
        if isinstance(error, TableNotFoundError):
            return error_response("NOT_FOUND", f"Table not found: {table_id}")
        return error_response("DATABASE_ERROR", f"Failed to get table: {error}")

    current_table = table_result.unwrap()

    # Check if table is already closed (terminal state)
    if is_terminal(current_table.status):
        return error_response(
            "OPERATION_NOT_ALLOWED",
            "Cannot perform control action on closed table. Closed is a terminal state.",
            {"table_status": current_table.status.value},
        )

    # Validate and compute new status
    new_status, validation_error = _validate_control_action(action, current_table.status)
    if validation_error is not None:
        return validation_error
    assert new_status is not None  # Guaranteed by no validation error

    # Create speaker and append CONTROL saying
    speaker = _create_control_speaker(speaker_name, patron_id)
    control_saying, saying_error = _append_control_saying(conn, table_id, action, reason, speaker)
    if saying_error is not None:
        return saying_error
    assert control_saying is not None  # Guaranteed by no saying_error

    # Update table status
    now = datetime.now(UTC)
    table_update = TableUpdate(
        question=current_table.question,
        context=current_table.context,
        status=new_status,
    )

    update_result = update_table(
        conn=conn,
        table_id=TableId(table_id),
        update=table_update,
        expected_version=current_table.version,
        now=now,
    )
    if isinstance(update_result, Failure):
        error = update_result.failure()
        if isinstance(error, VersionConflictError):
            return error_response(
                "VERSION_CONFLICT",
                "Table version conflict during control operation",
                {
                    "expected_version": error.expected_version,
                    "actual_version": error.current_version,
                },
            )
        return error_response("DATABASE_ERROR", f"Failed to update table status: {error}")

    response_data = _build_control_response(new_status, control_saying.sequence)

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

    return success_response(response_data)


# @shell_complexity: 8 branches for table lookup + version check + update + dedup + error paths
# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
def table_update(
    table_id: str,
    expected_version: int,
    patch: dict[str, Any],
    speaker_name: str,
    patron_id: str | None = None,
    dedup_id: str | None = None,
) -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())
    table_update_actor_metadata = _build_table_update_actor_metadata(speaker_name, patron_id)

    # Resource key for idempotency scope
    resource_key = f"update:{table_id}"

    # Check idempotency key if provided
    if dedup_id is not None:
        idempotency_result = check_idempotency_key(conn, resource_key, "table_update", dedup_id)
        if isinstance(idempotency_result, Failure):
            error = idempotency_result.failure()
            return error_response("DATABASE_ERROR", f"Failed to check idempotency key: {error}")

        cached_response = idempotency_result.unwrap()
        if cached_response is not None:
            log_dedup_hit(logger, "table_update", resource_key, dedup_id)
            return success_response(cached_response["data"])

    # Get current table
    table_result = get_table(conn, TableId(table_id))
    if isinstance(table_result, Failure):
        error = table_result.failure()
        if isinstance(error, TableNotFoundError):
            return error_response("NOT_FOUND", f"Table not found: {table_id}")
        return error_response("DATABASE_ERROR", f"Failed to get table: {error}")

    current_table = table_result.unwrap()

    # Apply patch to create update (only supported fields)
    table_update, patch_error = _apply_table_patch(current_table, patch)
    if patch_error is not None:
        return patch_error

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
            return error_response("NOT_FOUND", f"Table not found: {table_id}")
        if isinstance(error, VersionConflictError):
            return error_response(
                "VERSION_CONFLICT",
                "Table version conflict",
                {
                    "expected_version": error.expected_version,
                    "actual_version": error.current_version,
                    "table": _build_table_dict(current_table),
                },
            )
        return error_response("DATABASE_ERROR", f"Failed to update table: {error}")

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

    return success_response(response_data)


# @shell_complexity: 10 branches for table lookup + long-poll loop + timeout + backoff + error handling
# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
# Note: FastMCP supports async tools - using async def for blocking wait
async def table_wait(
    table_id: str,
    since_sequence: int = -1,
    wait_ms: int = DEFAULT_WAIT_MS,
    limit: int = 50,
    include_table: bool = False,
) -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    conn = next(get_mcp_db())

    # Verify table exists
    table_result = get_table(conn, TableId(table_id))
    if isinstance(table_result, Failure):
        error = table_result.failure()
        if isinstance(error, TableNotFoundError):
            return error_response("NOT_FOUND", f"Table not found: {table_id}")
        return error_response("DATABASE_ERROR", f"Failed to get table: {error}")

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
            return error_response("DATABASE_ERROR", f"Failed to list sayings: {error}")

        sayings = result.unwrap()

        if sayings:
            # Found new sayings - return them
            next_sequence = _compute_next_sequence(sayings, since_sequence)
            loop = _record_wait_result(table_id, got_sayings=True)

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

            return success_response(response_data)

        # Wait before next poll
        remaining = end_time - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(poll_interval_seconds, remaining))

    # Timeout - return empty with current next_sequence (same shape as table_listen)
    next_sequence = _compute_next_sequence([], since_sequence)
    loop = _record_wait_result(table_id, got_sayings=False)
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

    return success_response(response_data)


# =============================================================================
# Seat Tools
# =============================================================================


# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
def seat_heartbeat(
    table_id: str,
    patron_id: str | None = None,
    state: Literal["running", "idle", "done"] | None = None,
    ttl_ms: int | None = None,
    dedup_id: str | None = None,
    seat_id: str | None = None,
) -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    return _seat_heartbeat_impl(table_id, patron_id, state, ttl_ms, dedup_id, seat_id)


# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
def seat_list(
    table_id: str,
    active_only: bool = True,
) -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    return _seat_list_impl(table_id, active_only)


# =============================================================================
# Proxy Control Tools
# =============================================================================


# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
async def connect(url: str | None = None, token: str | None = None) -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    return await _connect_impl(url, token)


# @invar:allow shell_result: entrypoints.py - MCP tool returns dict responses, not Result[T, E]
def connection_status() -> dict[str, Any]:
    """Implementation detail for MCP tool behavior."""
    return _connection_status_impl()
