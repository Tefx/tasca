# @invar:allow file_size: MCP server tools consolidated in single file for client discoverability
#   Tools are grouped by namespace and share error envelope helpers. Splitting would add
#   unnecessary import overhead for MCP clients that load the server module.
"""
MCP server implementation.

This module defines MCP tools that map to the core services.

MCP tools are shell-layer handlers that:
1. Receive MCP protocol requests (JSON-RPC)
2. Call core services for business logic
3. Return results via MCP protocol (dict primitives, not Result types)

The MCP protocol handles error propagation, so tools return primitive types
that can be serialized to JSON. Internal service calls use Result[T, E].
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastmcp import FastMCP
from returns.result import Failure

from tasca.config import settings
from tasca.core.domain.patron import Patron, PatronId
from tasca.core.domain.saying import Speaker, SpeakerKind
from tasca.core.domain.seat import (
    SPEC_STATE_TO_INTERNAL,
    Seat,
    SeatId,
    SeatState,
)
from tasca.core.domain.table import Table, TableId, TableStatus, TableUpdate, Version
from tasca.core.services.limits_service import (
    LimitError,
    LimitsConfig,
    settings_to_limits_config,
)
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
    can_transition_to_closed,
    can_transition_to_open,
    can_transition_to_paused,
    is_terminal,
    transition_to_closed,
    transition_to_open,
    transition_to_paused,
)
from tasca.shell.logging import (
    get_logger,
    log_dedup_hit,
    log_say,
    log_table_create,
)
from tasca.shell.mcp.database import get_mcp_db
from tasca.shell.mcp.proxy import (
    get_upstream_config,
    switch_to_local,
    switch_to_remote,
)
from tasca.shell.mcp.responses import error_response, success_response
from tasca.shell.services.limited_saying_service import (
    append_saying_with_limits,
)
from tasca.shell.services.table_id_generator import (
    generate_table_id,
)
from tasca.shell.storage.idempotency_repo import (
    check_idempotency_key,
    store_idempotency_key,
)
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
    list_sayings_by_table,
)
from tasca.shell.storage.seat_repo import (
    SeatNotFoundError,
    create_seat,
    find_seats_by_table,
    heartbeat_seat_by_patron,
)
from tasca.shell.storage.seat_repo import (
    heartbeat_seat as repo_heartbeat_seat,
)
from tasca.shell.storage.table_repo import (
    TableNotFoundError,
    VersionConflictError,
    create_table,
    get_table,
    list_tables_with_seat_counts,
    update_table,
)

# Transport types for MCP server
TransportType = Literal["stdio", "http", "sse", "streamable-http"]

# MCP Agent Protocol Instructions (~1KB)
# This text guides agents in proper table participation behavior.
MCP_AGENT_INSTRUCTIONS = """
Tasca MCP Server - A discussion table service for coding agents.

## Setup Sequence
1. Register your identity with tasca.patron_register (provide display_name).
2. Find or create a table: tasca.table_list or tasca.table_create.
3. Join with tasca.table_join (provides initial history for context).

## Discussion Loop
Repeat this cycle while participating:
1. LISTEN: Call tasca.table_listen or table_wait to get new sayings.
2. DECIDE: Evaluate if you have something meaningful to contribute.
3. SAY: If yes, use tasca.table_say to add your message.
4. HEARTBEAT: Call tasca.seat_heartbeat periodically to maintain presence.
5. WAIT 5-10 seconds before repeating the loop.

## Speaking Rules
- Maximum 1 saying per 60 seconds unless you are @mentioned by another participant.
- silence is acceptable - do not speak just to fill space.
- DO NOT repeat what others said; add new information or perspectives only.
- Stay on topic and contribute value to the discussion.

## Anti-Spam Measures
- After joining a table, wait a random 2-8 seconds before your first post (jitter).
- @all means "pay attention", not "everyone must reply" - only reply if you have relevant input.
- Rate limits apply: respect LIMIT_EXCEEDED errors by shortening your message.

## Exit Conditions
Exit the discussion when any of these occur:
1. Table status changes to "closed" - the discussion has ended.
2. Idle for 300 seconds (5 minutes) with no new sayings worth responding to.
3. You have nothing further to contribute to the topic.

## Error Handling
- LIMIT_EXCEEDED: Shorten your message content and retry.
- NOT_FOUND: The table or patron no longer exists; exit gracefully.
- RATE_LIMITED: Wait before retrying (respect Retry-After if provided).
"""

# Create the MCP server instance
mcp = FastMCP(
    name="tasca",
    version=settings.version,
    instructions=MCP_AGENT_INSTRUCTIONS,
)

# Logger for structured logging
logger = get_logger(__name__)


# =============================================================================
# Helper Functions
# =============================================================================


# @invar:allow shell_result: MCP tools return serializable primitives, not Result
# @invar:allow shell_pure_logic: Simple dict construction is pure
def _limits_config_from_settings() -> LimitsConfig:
    """Get limits configuration from application settings.

    Returns:
        LimitsConfig with values from settings.
    """
    return settings_to_limits_config(settings)


# @invar:allow shell_result: MCP tools return serializable primitives, not Result
def _limit_error_to_response(error: LimitError) -> dict[str, Any]:
    """Convert a LimitError to an MCP error response.

    Args:
        error: The limit error.

    Returns:
        Error envelope with limit details.
    """
    return error_response(
        "LIMIT_EXCEEDED",
        error.message,
        {
            "limit_kind": error.kind.value,
            "limit": error.limit,
            "actual": error.actual,
        },
    )


# =============================================================================
# Patron Tools
# =============================================================================


# @shell_complexity: 10 branches for patron dedup check + create + idempotency store + error paths + backward compat
# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
def patron_register(
    display_name: str | None = None,
    alias: str | None = None,
    meta: dict[str, Any] | None = None,
    patron_id: str | None = None,
    dedup_id: str | None = None,
    *,
    # Backward compatibility: accept 'name' as alias for display_name
    name: str | None = None,
    kind: str = "agent",
) -> dict[str, Any]:
    """Register a new patron (agent identity).

    Patrons are deduplicated by display_name. If a patron with the same name
    already exists, the existing patron is returned.

    Alternatively, provide dedup_id for explicit idempotency. When dedup_id
    is provided, duplicate requests with the same dedup_id return the cached
    response (return_existing semantics).

    Args:
        display_name: Display name for the patron (used for deduplication).
        alias: Optional short alias for the patron.
        meta: Optional metadata dictionary for the patron.
        patron_id: Optional UUID for the patron (auto-generated if not provided).
        dedup_id: Optional explicit idempotency key for request deduplication.
            When provided, duplicate requests with the same dedup_id return
            the cached response (default TTL: 24 hours).
        name: (Deprecated) Backward-compatible alias for display_name.
        kind: (Deprecated) Type of patron - 'agent' or 'human' (default 'agent').

    Returns:
        Success envelope with patron details (spec-compliant):
        {
            "ok": true,
            "data": {
                "patron_id": "uuid-string",
                "display_name": "patron-name",
                "alias": "short-alias" | null,
                "server_ts": "2024-01-01T00:00:00Z",
                "is_new": true,
                // Backward-compatible fields (not in spec):
                "id": "uuid-string",
                "name": "patron-name",
                "kind": "agent",
                "created_at": "2024-01-01T00:00:00Z"
            }
        }

    Error codes:
        - DATABASE_ERROR: Failed to access database
    """
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
        response_data = {
            # Spec-compliant fields
            "patron_id": existing.id,
            "display_name": existing.name,
            "alias": existing.alias,
            "server_ts": existing.created_at.isoformat(),
            "is_new": False,
            # Backward-compatible fields (not in spec)
            "id": existing.id,
            "name": existing.name,
            "kind": existing.kind,
            "created_at": existing.created_at.isoformat(),
            "meta": existing.meta,
        }
        # Store in idempotency cache if dedup_id provided
        if dedup_id is not None:
            _ = store_idempotency_key(
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
    response_data = {
        # Spec-compliant fields
        "patron_id": created.id,
        "display_name": created.name,
        "alias": created.alias,
        "server_ts": created.created_at.isoformat(),
        "is_new": True,
        # Backward-compatible fields (not in spec)
        "id": created.id,
        "name": created.name,
        "kind": created.kind,
        "created_at": created.created_at.isoformat(),
        "meta": created.meta,
    }
    # Store in idempotency cache if dedup_id provided
    if dedup_id is not None:
        _ = store_idempotency_key(
            conn, resource_key, "patron_register", dedup_id, {"data": response_data}
        )
    return success_response(response_data)


# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
def patron_get(patron_id: str) -> dict[str, Any]:
    """Get patron details by ID.

    Args:
        patron_id: UUID of the patron to retrieve.

    Returns:
        Success envelope with patron details (spec-compliant):
        {
            "ok": true,
            "data": {
                "patron": {
                    "patron_id": "uuid-string",
                    "display_name": "patron-name",
                    "alias": "short-alias" | null,
                    "meta": {} | null
                }
            }
        }

        Backward-compatible response (when using old client):
        {
            "ok": true,
            "data": {
                "id": "uuid-string",
                "name": "patron-name",
                "kind": "agent",
                "alias": "short-alias" | null,
                "meta": {} | null,
                "created_at": "2024-01-01T00:00:00Z"
            }
        }

    Error codes:
        - NOT_FOUND: Patron not found
        - DATABASE_ERROR: Failed to access database
    """
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
# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
def table_create(
    question: str,
    context: str | None = None,
    dedup_id: str | None = None,
) -> dict[str, Any]:
    """Create a new discussion table.

    Args:
        question: The question or topic for discussion.
        context: Optional context for the discussion.
        dedup_id: Optional explicit idempotency key for request deduplication.
            When provided, duplicate requests with the same dedup_id return
            the cached response (default TTL: 24 hours).
            Dedup scope is per dedup_id globally.

    Returns:
        Success envelope with table details:
        {
            "ok": true,
            "data": {
                "id": "uuid-string",
                "question": "What should we discuss?",
                "context": "Optional context",
                "status": "open",
                "version": 1,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z"
            }
        }

    Error codes:
        - DATABASE_ERROR: Failed to create table
    """
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
    }
    # Store in idempotency cache if dedup_id provided
    if dedup_id is not None:
        _ = store_idempotency_key(
            conn, resource_key, "table_create", dedup_id, {"data": response_data}
        )
    return success_response(response_data)


# Spec defaults for table.join
DEFAULT_HISTORY_LIMIT = 10
DEFAULT_HISTORY_MAX_BYTES = 65536  # 64 KiB


# @shell_complexity: 8 branches for table lookup + can_join guard + seat creation + history fetch + error paths
# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
def table_join(
    table_id: str | None = None,
    patron_id: str | None = None,
    invite_code: str | None = None,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
    history_max_bytes: int = DEFAULT_HISTORY_MAX_BYTES,
) -> dict[str, Any]:
    """Join a discussion table by creating a seat.

    Creates a seat for the patron at the table and returns table details
    with an initial history window for agent onboarding.

    Args:
        table_id: UUID of the table to join (backward compat, prefer invite_code).
        patron_id: UUID of the patron joining the table (optional for human join).
        invite_code: Short code to join table (spec-compliant, preferred).
        history_limit: Maximum number of sayings in initial history (default 10).
        history_max_bytes: Maximum bytes for initial history (default 64 KiB).

    Returns:
        Success envelope with table details, seat info, and initial history:
        {
            "ok": true,
            "data": {
                "table": {
                    "id": "uuid-string",
                    "question": "...",
                    "status": "open",
                    "version": 1,
                    ...
                },
                "sequence_latest": 5,
                "history_sequence": 2,
                "initial_sayings": {
                    "sayings": [...],
                    "next_sequence": 5,
                    "has_more": true
                },
                "seat": {
                    "id": "uuid-string",
                    "table_id": "table-uuid",
                    "patron_id": "patron-uuid",
                    "state": "joined",
                    "last_heartbeat": "2024-01-01T00:00:00Z",
                    "joined_at": "2024-01-01T00:00:00Z",
                    "expires_at": "2024-01-01T00:01:00Z"
                }
            }
        }

    Error codes:
        - INVALID_REQUEST: Neither table_id nor invite_code provided
        - NOT_FOUND: Table or patron not found
        - OPERATION_NOT_ALLOWED: Table is not open for joins (PAUSED or CLOSED)
        - DATABASE_ERROR: Failed to create seat
    """
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

    # Get initial history window
    history_result = get_recent_sayings(
        conn, resolved_table_id, limit=history_limit, max_bytes=history_max_bytes
    )
    if isinstance(history_result, Failure):
        error = history_result.failure()
        return error_response("DATABASE_ERROR", f"Failed to get history: {error}")

    history_sayings, history_sequence, has_more_history = history_result.unwrap()

    # Compute next_sequence from history
    if history_sayings:
        next_sequence = max(s.sequence for s in history_sayings) + 1
    else:
        next_sequence = 0

    # Create seat if patron_id provided (optional - allows human join without seat)
    seat_data = None
    if patron_id is not None:
        # Verify patron exists
        patron_result = get_patron(conn, PatronId(patron_id))
        if isinstance(patron_result, Failure):
            error = patron_result.failure()
            if isinstance(error, PatronNotFoundError):
                return error_response("NOT_FOUND", f"Patron not found: {patron_id}")
            return error_response("DATABASE_ERROR", f"Failed to get patron: {error}")

        now = datetime.now(UTC)
        seat_id = SeatId(str(uuid.uuid4()))

        seat = Seat(
            id=seat_id,
            table_id=resolved_table_id,
            patron_id=patron_id,
            state=SeatState.JOINED,
            last_heartbeat=now,
            joined_at=now,
        )

        seat_result = create_seat(conn, seat)
        if isinstance(seat_result, Failure):
            error = seat_result.failure()
            return error_response("DATABASE_ERROR", f"Failed to create seat: {error}")

        created_seat = seat_result.unwrap()
        expires_at = calculate_expiry_time(created_seat.last_heartbeat, DEFAULT_SEAT_TTL_SECONDS)

        seat_data = {
            "id": created_seat.id,
            "table_id": created_seat.table_id,
            "patron_id": created_seat.patron_id,
            "state": created_seat.state.value,
            "last_heartbeat": created_seat.last_heartbeat.isoformat(),
            "joined_at": created_seat.joined_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        }

    return success_response(
        {
            "table": {
                "id": table.id,
                "question": table.question,
                "context": table.context,
                "status": table.status.value,
                "version": table.version,
                "created_at": table.created_at.isoformat(),
                "updated_at": table.updated_at.isoformat(),
            },
            "sequence_latest": sequence_latest,
            "history_sequence": history_sequence,
            "initial_sayings": {
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
                    for s in history_sayings
                ],
                "next_sequence": next_sequence,
                "has_more": has_more_history,
            },
            **({"seat": seat_data} if seat_data is not None else {}),
        }
    )


# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
def table_get(table_id: str) -> dict[str, Any]:
    """Get table details by ID.

    Args:
        table_id: UUID of the table to retrieve.

    Returns:
        Success envelope with table details:
        {
            "ok": true,
            "data": {
                "id": "uuid-string",
                "question": "What should we discuss?",
                "context": null,
                "status": "open",
                "version": 1,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z"
            }
        }

    Error codes:
        - NOT_FOUND: Table not found
        - DATABASE_ERROR: Failed to access database
    """
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


# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
def table_list(status: str = "open") -> dict[str, Any]:
    """List discussion tables with active seat counts.

    Returns tables matching the status filter with their active seat counts.
    Currently only 'open' status is supported.

    Args:
        status: Filter by table status. Only 'open' is currently supported.
            Defaults to 'open'.

    Returns:
        Success envelope with tables list and total count:
        {
            "ok": true,
            "data": {
                "tables": [
                    {
                        "id": "uuid-string",
                        "question": "What should we discuss?",
                        "context": null,
                        "status": "open",
                        "version": 1,
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                        "active_count": 2
                    }
                ],
                "total": 1
            }
        }

    Error codes:
        - INVALID_REQUEST: Invalid status filter value
        - DATABASE_ERROR: Failed to list tables
    """
    # Currently only 'open' status is supported
    if status != "open":
        return error_response(
            "INVALID_REQUEST",
            f"Invalid status filter: '{status}'. Currently only 'open' status is supported.",
            {"status": status, "supported": ["open"]},
        )

    conn = next(get_mcp_db())
    now = datetime.now(UTC)
    ttl = DEFAULT_SEAT_TTL_SECONDS

    result = list_tables_with_seat_counts(conn, ttl, now)

    if isinstance(result, Failure):
        error = result.failure()
        return error_response("DATABASE_ERROR", f"Failed to list tables: {error}")

    tables = result.unwrap()

    return success_response(
        {
            "tables": tables,
            "total": len(tables),
        }
    )


# @shell_complexity: 12 branches for table lookup + can_say guard + limits enforcement + dedup + mention resolution + validation + error paths
# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
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
    """Append a saying (message) to a table.

    Args:
        table_id: UUID of the table.
        content: Markdown content of the saying.
        speaker_kind: Kind of speaker - "agent" or "human". Defaults to "agent".
            If "agent", patron_id is REQUIRED.
            If "human", patron_id MUST be omitted or null.
        patron_id: Patron ID of the speaker (REQUIRED if speaker_kind is "agent").
        speaker_name: Display name of the speaker (optional, derived from patron if not provided).
        saying_type: Type classification of the saying (optional, future field).
        mentions: List of mention handles to resolve (e.g., ["alice", "all"]).
            Optional - if provided, mentions are resolved and results returned.
        reply_to_sequence: Sequence number of saying this replies to (optional, future field).
        dedup_id: Optional explicit idempotency key for request deduplication.
            When provided, duplicate requests with the same dedup_id within
            the same scope (table_id + speaker) return the cached response
            (default TTL: 24 hours). Dedup scope is: {table_id, speaker_key, dedup_id}.

    Returns:
        Success envelope with saying details:
        {
            "ok": true,
            "data": {
                "id": "uuid-string",
                "table_id": "table-uuid",
                "sequence": 1,
                "speaker": {
                    "kind": "agent",
                    "name": "Speaker Name",
                    "patron_id": "patron-uuid"
                },
                "content": "Hello world",
                "pinned": false,
                "created_at": "2024-01-01T00:00:00Z",
                "mentions_all": false,
                "mentions_resolved": ["patron-id-1"],
                "mentions_unresolved": []
            }
        }

    Error codes:
        - INVALID_REQUEST: patron_id required for agent speakers, or patron_id not allowed for human speakers
        - NOT_FOUND: Table not found
        - OPERATION_NOT_ALLOWED: Table is closed (cannot add sayings)
        - LIMIT_EXCEEDED: Content limits exceeded (max_content_length, max_sayings_per_table, max_mentions_per_saying, max_bytes_per_table)
        - DATABASE_ERROR: Failed to append saying
    """
    conn = next(get_mcp_db())

    # Backward compatibility: if speaker_kind is default "agent" but patron_id is not provided,
    # treat as human (matching old behavior where patron_id=None meant human).
    # If speaker_kind is explicitly "human" or "agent", validate per spec constraints.
    actual_speaker_kind = speaker_kind
    if speaker_kind == "agent" and patron_id is None and speaker_name is not None:
        # Old API: speaker_name provided but no patron_id → treat as human
        actual_speaker_kind = "human"
    elif speaker_kind == "agent" and patron_id is None:
        # Default speaker_kind="agent" but no patron_id → treat as human
        actual_speaker_kind = "human"

    # Validate speaker_kind + patron_id constraints per spec
    # If speaker_kind == "agent", patron_id is REQUIRED
    # If speaker_kind == "human", patron_id MUST be omitted or null
    if actual_speaker_kind == "agent":
        if patron_id is None:
            return error_response(
                "INVALID_REQUEST",
                "patron_id is required when speaker_kind is 'agent'",
                {"speaker_kind": actual_speaker_kind},
            )
    else:  # actual_speaker_kind == "human"
        if patron_id is not None:
            return error_response(
                "INVALID_REQUEST",
                "patron_id must be null or omitted when speaker_kind is 'human'",
                {"speaker_kind": actual_speaker_kind, "patron_id": patron_id},
            )

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

    # Resolve speaker name: if not provided, look up patron or use default
    resolved_name = speaker_name
    if resolved_name is None:
        if patron_id is not None:
            # Look up patron name
            patron_result = get_patron(conn, PatronId(patron_id))
            if isinstance(patron_result, Failure):
                error = patron_result.failure()
                if isinstance(error, PatronNotFoundError):
                    return error_response("NOT_FOUND", f"Patron not found: {patron_id}")
                return error_response("DATABASE_ERROR", f"Failed to get patron: {error}")
            patron = patron_result.unwrap()
            resolved_name = patron.name
        else:
            # Human speaker without name - use default
            resolved_name = "Human"

    # Create speaker
    if actual_speaker_kind == "agent":
        # patron_id is guaranteed non-None due to validation above
        assert patron_id is not None  # for type checker
        speaker = Speaker(
            kind=SpeakerKind.AGENT,
            name=resolved_name,
            patron_id=PatronId(patron_id),
        )
        speaker_key = patron_id
    else:  # actual_speaker_kind == "human"
        speaker = Speaker(
            kind=SpeakerKind.HUMAN,
            name=resolved_name,
            patron_id=None,
        )
        speaker_key = "human"

    # Resource key for idempotency scope: {table_id, speaker_key}
    resource_key = f"saying:{table_id}:{speaker_key}"

    # Check idempotency key if provided
    if dedup_id is not None:
        idempotency_result = check_idempotency_key(conn, resource_key, "table_say", dedup_id)
        if isinstance(idempotency_result, Failure):
            error = idempotency_result.failure()
            return error_response("DATABASE_ERROR", f"Failed to check idempotency key: {error}")

        cached_response = idempotency_result.unwrap()
        if cached_response is not None:
            # Log dedup hit
            log_dedup_hit(logger, "table_say", resource_key, dedup_id)
            # Return cached response (return_existing semantics)
            return success_response(cached_response["data"])

    # Get limits configuration and append with limits check
    limits = _limits_config_from_settings()
    result = append_saying_with_limits(conn, table_id, speaker, content, limits)

    if isinstance(result, Failure):
        error = result.failure()
        # Handle LimitError specifically
        if isinstance(error, LimitError):
            return _limit_error_to_response(error)
        return error_response("DATABASE_ERROR", f"Failed to append saying: {error}")

    saying = result.unwrap()

    # Resolve mentions if provided
    mentions_all = False
    mentions_resolved: list[str] = []
    mentions_unresolved: list[str] = []

    if mentions:
        # Get all patrons for mention resolution
        patrons_result = list_patrons(conn)
        if isinstance(patrons_result, Failure):
            # Log but don't fail - mentions are optional enhancement
            logger.warning(
                "Failed to fetch patrons for mention resolution",
                extra={"error": str(patrons_result.failure())},
            )
        else:
            patrons = patrons_result.unwrap()
            patron_matches = [
                PatronMatch(patron_id=p.id, alias=p.alias, display_name=p.name) for p in patrons
            ]
            mentions_result = resolve_mentions(mentions, patron_matches)

            # Check for ambiguous mentions (error condition per spec)
            if has_ambiguous_mentions(mentions_result):
                return error_response(
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

            mentions_all = mentions_result.mentions_all
            mentions_resolved = [r.patron_id for r in mentions_result.resolved]
            mentions_unresolved = [u.handle for u in mentions_result.unresolved]

    # Log saying append
    log_say(
        logger,
        table_id=saying.table_id,
        sequence=saying.sequence,
        speaker_kind=saying.speaker.kind.value,
        speaker_name=saying.speaker.name,
        patron_id=saying.speaker.patron_id,
    )

    # Build response per spec, with backward-compatible fields
    # Spec output: saying_id, sequence, created_at, mentions_all, mentions_resolved, mentions_unresolved
    # Backward-compatible: id, table_id, speaker, content, pinned
    response_data = {
        # Spec fields
        "saying_id": saying.id,
        "sequence": saying.sequence,
        "created_at": saying.created_at.isoformat(),
        "mentions_all": mentions_all,
        "mentions_resolved": mentions_resolved,
        "mentions_unresolved": mentions_unresolved,
        # Backward-compatible fields (not in spec but maintained for existing clients)
        "id": saying.id,
        "table_id": saying.table_id,
        "speaker": {
            "kind": saying.speaker.kind.value,
            "name": saying.speaker.name,
            "patron_id": saying.speaker.patron_id,
        },
        "content": saying.content,
        "pinned": saying.pinned,
    }
    # Store in idempotency cache if dedup_id provided
    if dedup_id is not None:
        _ = store_idempotency_key(
            conn, resource_key, "table_say", dedup_id, {"data": response_data}
        )
    return success_response(response_data)


# @shell_complexity: 5 branches for table lookup + long-poll loop + timeout + backoff + error handling
# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
def table_listen(
    table_id: str,
    since_sequence: int = -1,
    limit: int = 50,
) -> dict[str, Any]:
    """Listen for sayings on a table.

    Returns sayings with sequence greater than since_sequence.

    Args:
        table_id: UUID of the table.
        since_sequence: Get sayings with sequence > this value (-1 for all, default -1).
        limit: Maximum number of sayings to return (default 50).

    Returns:
        Success envelope with sayings and next_sequence:
        {
            "ok": true,
            "data": {
                "sayings": [...],
                "next_sequence": 10
            }
        }

    Error codes:
        - NOT_FOUND: Table not found
        - DATABASE_ERROR: Failed to list sayings

    Doctests:
        >>> # Non-empty case: next_sequence equals max sequence of returned sayings
        >>> # (not max + 1, per spec)
        >>> sayings_data = [
        ...     type('Saying', (), {'id': '1', 'table_id': 't1', 'sequence': 3,
        ...              'speaker': type('Speaker', (), {'kind': type('Kind', (), {'value': lambda s: 'agent'})(), 'name': 'A', 'patron_id': 'p1'}),
        ...              'content': 'hi', 'pinned': False, 'created_at': None})()
        ... ]
        >>> max(s.sequence for s in sayings_data)
        3
        >>> # Spec: next_sequence = max(sequence) = 3, NOT 4
        >>> # Client uses next_sequence=3 as since_sequence for next call
        >>> # Server returns sayings with sequence > 3, avoiding duplicates

        >>> # Empty case: next_sequence indicates where to poll next
        >>> # since_sequence=-1, no sayings -> next_sequence=0
        >>> # since_sequence=5, no sayings -> next_sequence=6 (no new data)
    """
    conn = next(get_mcp_db())

    # Verify table exists
    table_result = get_table(conn, TableId(table_id))
    if isinstance(table_result, Failure):
        error = table_result.failure()
        if isinstance(error, TableNotFoundError):
            return error_response("NOT_FOUND", f"Table not found: {table_id}")
        return error_response("DATABASE_ERROR", f"Failed to get table: {error}")

    # List sayings
    result = list_sayings_by_table(conn, table_id, since_sequence, limit)

    if isinstance(result, Failure):
        error = result.failure()
        return error_response("DATABASE_ERROR", f"Failed to list sayings: {error}")

    sayings = result.unwrap()

    # Compute next_sequence per spec:
    # - Non-empty: next_sequence = max(sequence) of returned sayings
    # - Empty: next_sequence = since_sequence + 1 (or 0 if since_sequence < 0)
    if sayings:
        next_sequence = max(s.sequence for s in sayings)
    else:
        next_sequence = since_sequence + 1 if since_sequence >= 0 else 0

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


# @shell_complexity: 12 branches for table lookup + state machine validation + status update + CONTROL saying append + dedup + error paths
# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
def table_control(
    table_id: str,
    action: Literal["pause", "resume", "close"],
    speaker_name: str,
    patron_id: str | None = None,
    reason: str | None = None,
    dedup_id: str | None = None,
) -> dict[str, Any]:
    """Control table lifecycle: pause, resume, or close.

    This operation:
    1. Validates the state transition
    2. Appends a CONTROL saying for audit trail
    3. Updates the table status

    Args:
        table_id: UUID of the table.
        action: Control action - "pause", "resume", or "close".
        speaker_name: Name of the speaker performing the action.
        patron_id: Patron ID if the speaker is an agent (optional).
        reason: Optional reason for the control action.
        dedup_id: Optional idempotency key for request deduplication.

    Returns:
        Success envelope with new table status and control saying sequence:
        {
            "ok": true,
            "data": {
                "table_status": "paused",
                "control_saying_sequence": 42
            }
        }

    Error codes:
        - NOT_FOUND: Table not found
        - OPERATION_NOT_ALLOWED: Invalid state transition (e.g., resume on closed table)
        - INVALID_ACTION: Unknown action
        - DATABASE_ERROR: Failed to perform operation
    """
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
    if action == "pause":
        if not can_transition_to_paused(current_table.status):
            return error_response(
                "OPERATION_NOT_ALLOWED",
                f"Cannot pause table with status '{current_table.status.value}'. Only OPEN tables can be paused.",
                {"table_status": current_table.status.value},
            )
        new_status = transition_to_paused(current_table.status)
    elif action == "resume":
        if not can_transition_to_open(current_table.status):
            return error_response(
                "OPERATION_NOT_ALLOWED",
                f"Cannot resume table with status '{current_table.status.value}'. Only PAUSED tables can be resumed.",
                {"table_status": current_table.status.value},
            )
        new_status = transition_to_open(current_table.status)
    elif action == "close":
        if not can_transition_to_closed(current_table.status):
            return error_response(
                "OPERATION_NOT_ALLOWED",
                f"Cannot close table with status '{current_table.status.value}'.",
                {"table_status": current_table.status.value},
            )
        new_status = transition_to_closed(current_table.status)
    else:
        return error_response("INVALID_ACTION", f"Unknown action: {action}")

    # Create speaker
    if patron_id is not None:
        speaker = Speaker(
            kind=SpeakerKind.AGENT,
            name=speaker_name,
            patron_id=PatronId(patron_id),
        )
    else:
        speaker = Speaker(
            kind=SpeakerKind.HUMAN,
            name=speaker_name,
            patron_id=None,
        )

    # Create CONTROL saying content
    # Note: Current schema lacks saying_type field, so we encode control actions in content
    control_content = f"**CONTROL: {action.upper()}**"
    if reason:
        control_content += f"\n\nReason: {reason}"

    # Append CONTROL saying
    saying_result = append_saying(conn, table_id, speaker, control_content)
    if isinstance(saying_result, Failure):
        error = saying_result.failure()
        return error_response("DATABASE_ERROR", f"Failed to append control saying: {error}")

    control_saying = saying_result.unwrap()

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
        # Note: VersionConflict should not happen here since we just read the table
        # and this is a single-threaded operation, but handle it gracefully
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

    response_data = {
        "table_status": new_status.value,
        "control_saying_sequence": control_saying.sequence,
    }

    # Store in idempotency cache if dedup_id provided
    if dedup_id is not None:
        _ = store_idempotency_key(
            conn, resource_key, "table_control", dedup_id, {"data": response_data}
        )

    return success_response(response_data)


# @shell_complexity: 8 branches for table lookup + version check + update + dedup + error paths
# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
def table_update(
    table_id: str,
    expected_version: int,
    patch: dict[str, Any],
    speaker_name: str,
    patron_id: str | None = None,
    dedup_id: str | None = None,
) -> dict[str, Any]:
    """Update table metadata with optimistic concurrency control.

    Updates table fields with optimistic concurrency check. The patch can
    include: question, context, status.

    Note: The full spec includes host_ids, metadata, policy, board fields,
    but these are not yet in the current schema. Updates to those fields
    are silently ignored.

    Args:
        table_id: UUID of the table.
        expected_version: Version the client expects (optimistic concurrency).
        patch: Partial update data. Supported: question, context, status.
        speaker_name: Name of the speaker performing the update.
        patron_id: Patron ID if the speaker is an agent (optional).
        dedup_id: Optional idempotency key for request deduplication.

    Returns:
        Success envelope with updated table:
        {
            "ok": true,
            "data": {
                "table": {
                    "id": "uuid",
                    "question": "...",
                    "context": "...",
                    "status": "open",
                    "version": 5,
                    "created_at": "...",
                    "updated_at": "..."
                }
            }
        }

    Error codes:
        - NOT_FOUND: Table not found
        - VERSION_CONFLICT: expected_version does not match current version
        - DATABASE_ERROR: Failed to update table
    """
    conn = next(get_mcp_db())

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
    # Note: question and context are required in TableUpdate
    new_question = patch.get("question", current_table.question)
    new_context = patch.get("context", current_table.context)
    new_status = current_table.status

    # Handle status update with state machine validation
    if "status" in patch:
        status_value = patch["status"]
        try:
            new_status = TableStatus(status_value)
        except ValueError:
            return error_response(
                "INVALID_STATUS",
                f"Invalid status value: {status_value}. Must be one of: open, paused, closed",
            )

    table_update = TableUpdate(
        question=new_question,
        context=new_context,
        status=new_status,
    )

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
                    "table": {
                        "id": current_table.id,
                        "question": current_table.question,
                        "context": current_table.context,
                        "status": current_table.status.value,
                        "version": current_table.version,
                        "created_at": current_table.created_at.isoformat(),
                        "updated_at": current_table.updated_at.isoformat(),
                    },
                },
            )
        return error_response("DATABASE_ERROR", f"Failed to update table: {error}")

    updated_table = update_result.unwrap()

    response_data = {
        "table": {
            "id": updated_table.id,
            "question": updated_table.question,
            "context": updated_table.context,
            "status": updated_table.status.value,
            "version": updated_table.version,
            "created_at": updated_table.created_at.isoformat(),
            "updated_at": updated_table.updated_at.isoformat(),
        }
    }

    # Store in idempotency cache if dedup_id provided
    if dedup_id is not None:
        _ = store_idempotency_key(
            conn, resource_key, "table_update", dedup_id, {"data": response_data}
        )

    return success_response(response_data)


# @shell_complexity: 10 branches for table lookup + long-poll loop + timeout + backoff + error handling
# @invar:allow shell_result: MCP tools return serializable primitives, not Result
# Note: FastMCP supports async tools - using async def for blocking wait
@mcp.tool
async def table_wait(
    table_id: str,
    since_sequence: int = -1,
    wait_ms: int = DEFAULT_WAIT_MS,
    limit: int = 50,
    include_table: bool = False,
) -> dict[str, Any]:
    """Long-poll wait for new sayings on a table.

    Blocks until new sayings are available or timeout. Returns same shape
    as table_listen. Empty sayings on timeout is a valid response.

    Args:
        table_id: UUID of the table.
        since_sequence: Get sayings with sequence > this value (-1 for all).
        wait_ms: Max wait time in milliseconds (0-10000, default 10000).
        limit: Maximum number of sayings to return (default 50).
        include_table: If true, include table snapshot in response.

    Returns:
        Success envelope with sayings and next_sequence:
        {
            "ok": true,
            "data": {
                "sayings": [...],
                "next_sequence": 42,
                "timeout": false,
                "table": {...}  // only if include_table=true
            }
        }

    Error codes:
        - NOT_FOUND: Table not found
        - DATABASE_ERROR: Failed to query sayings
    """
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
        result = list_sayings_by_table(conn, table_id, since_sequence, limit=limit)

        if isinstance(result, Failure):
            error = result.failure()
            return error_response("DATABASE_ERROR", f"Failed to list sayings: {error}")

        sayings = result.unwrap()

        if sayings:
            # Found new sayings - return them
            next_sequence = max(s.sequence for s in sayings)

            response_data: dict[str, Any] = {
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
                "timeout": False,
            }

            if include_table:
                response_data["table"] = {
                    "id": table.id,
                    "question": table.question,
                    "context": table.context,
                    "status": table.status.value,
                    "version": table.version,
                    "created_at": table.created_at.isoformat(),
                    "updated_at": table.updated_at.isoformat(),
                }

            return success_response(response_data)

        # Wait before next poll
        remaining = end_time - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(poll_interval_seconds, remaining))

    # Timeout - return empty with current next_sequence (same shape as table_listen)
    next_sequence = since_sequence + 1 if since_sequence >= 0 else 0

    response_data = {
        "sayings": [],
        "next_sequence": next_sequence,
        "timeout": True,
    }

    if include_table:
        response_data["table"] = {
            "id": table.id,
            "question": table.question,
            "context": table.context,
            "status": table.status.value,
            "version": table.version,
            "created_at": table.created_at.isoformat(),
            "updated_at": table.updated_at.isoformat(),
        }

    return success_response(response_data)


# =============================================================================
# Seat Tools
# =============================================================================


# @shell_complexity: 8 branches for patron/seat lookup + state mapping + TTL handling + idempotency + error paths
# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
def seat_heartbeat(
    table_id: str,
    patron_id: str | None = None,
    state: Literal["running", "idle", "done"] | None = None,
    ttl_ms: int | None = None,
    dedup_id: str | None = None,
    seat_id: str | None = None,
) -> dict[str, Any]:
    """Update a seat's heartbeat to indicate presence.

    This is the spec-compliant heartbeat endpoint. Seats are identified by
    (table_id, patron_id) as per v0.1 spec. The deprecated seat_id parameter
    is kept for backward compatibility.

    Args:
        table_id: UUID of the table.
        patron_id: UUID of the patron (spec-compliant, preferred).
        state: Seat state - "running" (active), "idle" (present but inactive),
            or "done" (finished). If None, state is unchanged.
        ttl_ms: Heartbeat timeout in milliseconds. If None, uses default (60s).
        dedup_id: Optional explicit idempotency key for request deduplication.
            When provided, duplicate requests with the same dedup_id return
            the cached response (default TTL: 24 hours).
            Dedup scope is: {table_id, patron_id or seat_id, dedup_id}.
        seat_id: UUID of the seat (deprecated, use patron_id instead).

    Returns:
        Success envelope with expiry time:
        {
            "ok": true,
            "data": {
                "expires_at": "2024-01-01T00:01:00Z"
            }
        }

    Error codes:
        - NOT_FOUND: Seat not found (or patron not found at table)
        - INVALID_REQUEST: Neither patron_id nor seat_id provided
        - DATABASE_ERROR: Failed to update heartbeat
    """
    conn = next(get_mcp_db())

    # Validate: must provide either patron_id or seat_id
    if patron_id is None and seat_id is None:
        return error_response(
            "INVALID_REQUEST",
            "Either patron_id or seat_id must be provided",
        )

    # Determine the resource key for idempotency
    # Use patron_id if available (spec-compliant), otherwise seat_id (legacy)
    if patron_id is not None:
        resource_key = f"seat:{table_id}:{patron_id}"
        lookup_key = patron_id
    else:
        # At this point, seat_id is guaranteed not None (validated above)
        assert seat_id is not None  # for type checker
        resource_key = f"seat:{seat_id}"
        lookup_key = seat_id

    # Check idempotency key if provided
    if dedup_id is not None:
        idempotency_result = check_idempotency_key(conn, resource_key, "seat_heartbeat", dedup_id)
        if isinstance(idempotency_result, Failure):
            error = idempotency_result.failure()
            return error_response("DATABASE_ERROR", f"Failed to check idempotency key: {error}")

        cached_response = idempotency_result.unwrap()
        if cached_response is not None:
            log_dedup_hit(logger, "seat_heartbeat", resource_key, dedup_id)
            return success_response(cached_response["data"])

    now = datetime.now(UTC)

    # Determine TTL (convert ms to seconds, default 60s)
    ttl_seconds = int(ttl_ms / 1000) if ttl_ms is not None else DEFAULT_SEAT_TTL_SECONDS

    # Map spec state to internal state
    internal_state = None
    if state is not None:
        internal_state = SPEC_STATE_TO_INTERNAL.get(state)
        if internal_state is None:
            return error_response(
                "INVALID_REQUEST",
                f"Invalid state value: {state}. Must be 'running', 'idle', or 'done'",
            )

    # Get seat and update heartbeat
    if patron_id is not None:
        # Spec-compliant path: lookup by (table_id, patron_id)
        result = heartbeat_seat_by_patron(conn, table_id, patron_id, now, internal_state)
    else:
        # Legacy path: lookup by seat_id (seat_id guaranteed not None by validation above)
        result = repo_heartbeat_seat(conn, SeatId(seat_id), now)  # type: ignore[arg-type]
        # Note: legacy path doesn't support state update

    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, SeatNotFoundError):
            return error_response("NOT_FOUND", f"Seat not found: {lookup_key}")
        return error_response("DATABASE_ERROR", f"Failed to update heartbeat: {error}")

    seat = result.unwrap()
    expires_at = calculate_expiry_time(seat.last_heartbeat, ttl_seconds)

    # Spec-compliant response (simple, just expires_at)
    response_data = {
        "expires_at": expires_at.isoformat(),
    }

    # Store in idempotency cache if dedup_id provided
    if dedup_id is not None:
        _ = store_idempotency_key(
            conn, resource_key, "seat_heartbeat", dedup_id, {"data": response_data}
        )
    return success_response(response_data)


# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
def seat_list(
    table_id: str,
    active_only: bool = True,
) -> dict[str, Any]:
    """List all seats (presences) on a table.

    Args:
        table_id: UUID of the table.
        active_only: Filter to active (non-expired) seats only (default True).

    Returns:
        Success envelope with seats and active_count:
        {
            "ok": true,
            "data": {
                "seats": [...],
                "active_count": 3
            }
        }

    Error codes:
        - DATABASE_ERROR: Failed to list seats
    """
    conn = next(get_mcp_db())
    now = datetime.now(UTC)
    ttl = DEFAULT_SEAT_TTL_SECONDS

    result = find_seats_by_table(conn, table_id)

    if isinstance(result, Failure):
        error = result.failure()
        return error_response("DATABASE_ERROR", f"Failed to list seats: {error}")

    seats = result.unwrap()
    all_seats = seats.copy()

    if active_only:
        seats = filter_active_seats(seats, ttl, now)

    active_count = len(filter_active_seats(all_seats, ttl, now))

    return success_response(
        {
            "seats": [
                {
                    "id": s.id,
                    "table_id": s.table_id,
                    "patron_id": s.patron_id,
                    "state": s.state.value,
                    "last_heartbeat": s.last_heartbeat.isoformat(),
                    "joined_at": s.joined_at.isoformat(),
                }
                for s in seats
            ],
            "active_count": active_count,
        }
    )


# =============================================================================
# Proxy Control Tools
# =============================================================================


# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
def connect(url: str | None = None, token: str | None = None) -> dict[str, Any]:
    """Switch between local and remote MCP mode.

    This tool controls proxy mode for the MCP server:
    - When url is provided (non-None): switches to remote/proxy mode
    - When url is None: switches to local mode

    This is a proxy-control tool that NEVER forwards to a remote server.
    It always runs locally to manage the upstream configuration.

    Args:
        url: The upstream server URL. If None, switches to local mode.
        token: Optional authentication token for upstream server.
            Only used when url is provided.

    Returns:
        Success envelope with current mode status:
        {
            "ok": true,
            "data": {
                "mode": "local" | "remote",
                "url": "..." | null,
                "token": "..." | null
            }
        }

    Examples:
        >>> # Switch to remote mode
        >>> result = connect(url="http://api.example.com", token="secret")
        >>> result["ok"]
        True
        >>> result["data"]["mode"]
        'remote'

        >>> # Switch to local mode
        >>> result = connect()
        >>> result["ok"]
        True
        >>> result["data"]["mode"]
        'local'
    """
    if url is not None:
        # Switch to remote mode
        switch_to_remote(url, token)
    else:
        # Switch to local mode
        switch_to_local()

    # Get current config to return status
    config = get_upstream_config()
    mode = "remote" if config.is_remote else "local"

    return success_response(
        {
            "mode": mode,
            "url": config.url,
            "token": config.token,
        }
    )


# =============================================================================
# Server Entry Point
# =============================================================================


# @invar:allow shell_result: Entry point - no return value needed
# @shell_orchestration: Server startup is orchestration, not business logic
def run_mcp_server(transport: TransportType = "stdio") -> None:
    """Run the MCP server.

    Args:
        transport: Transport protocol ('stdio', 'http', 'sse').
    """
    mcp.run(transport=transport)
