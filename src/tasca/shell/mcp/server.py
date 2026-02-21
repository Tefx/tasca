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
from tasca.core.domain.seat import Seat, SeatId, SeatState
from tasca.core.domain.table import Table, TableId, TableStatus, TableUpdate, Version
from tasca.core.services.limits_service import (
    LimitError,
    LimitsConfig,
    settings_to_limits_config,
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
from tasca.shell.mcp.database import get_mcp_db
from tasca.shell.mcp.responses import error_response, success_response
from tasca.shell.services.limited_saying_service import (
    append_saying_with_limits,
)
from tasca.shell.storage.patron_repo import (
    PatronNotFoundError,
    create_patron,
    find_patron_by_name,
    get_patron,
)
from tasca.shell.storage.saying_repo import (
    append_saying,
    get_table_max_sequence,
    list_sayings_by_table,
)
from tasca.shell.storage.seat_repo import (
    SeatNotFoundError,
    create_seat,
    find_seats_by_table,
)
from tasca.shell.storage.seat_repo import (
    heartbeat_seat as repo_heartbeat_seat,
)
from tasca.shell.storage.table_repo import (
    TableNotFoundError,
    VersionConflictError,
    create_table,
    get_table,
    update_table,
)
from tasca.shell.storage.idempotency_repo import (
    check_idempotency_key,
    store_idempotency_key,
    DEFAULT_IDEMPOTENCY_TTL_SECONDS,
)
from tasca.shell.logging import (
    get_logger,
    log_dedup_hit,
    log_say,
    log_table_create,
)

# Transport types for MCP server
TransportType = Literal["stdio", "http", "sse", "streamable-http"]

# Create the MCP server instance
mcp = FastMCP(
    name="tasca",
    version=settings.version,
    instructions=(
        "Tasca MCP Server - A discussion table service for coding agents.\n\n"
        "Tools are organized by namespace:\n"
        "- tasca.patron.*: Patron identity management\n"
        "- tasca.table.*: Discussion table operations\n"
        "- tasca.seat.*: Seat presence management\n\n"
        "Start with tasca.patron_register to create your patron identity."
    ),
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


# @shell_complexity: 8 branches for patron dedup check + create + idempotency store + error paths
# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
def patron_register(
    name: str,
    kind: str = "agent",
    dedup_id: str | None = None,
) -> dict[str, Any]:
    """Register a new patron (agent identity).

    Patrons are deduplicated by name. If a patron with the same name
    already exists, the existing patron is returned.

    Alternatively, provide dedup_id for explicit idempotency. When dedup_id
    is provided with the same name, the cached response is returned (return_existing).

    Args:
        name: Name or identifier for the patron (used for deduplication).
        kind: Type of patron - 'agent' or 'human' (default 'agent').
        dedup_id: Optional explicit idempotency key for request deduplication.
            When provided, duplicate requests with the same dedup_id return
            the cached response (default TTL: 24 hours).

    Returns:
        Success envelope with patron details:
        {
            "ok": true,
            "data": {
                "id": "uuid-string",
                "name": "patron-name",
                "kind": "agent",
                "created_at": "2024-01-01T00:00:00Z",
                "is_new": true
            }
        }

    Error codes:
        - DATABASE_ERROR: Failed to access database
    """
    conn = next(get_mcp_db())

    # Resource key for idempotency scope (patron registration uses name as scope)
    resource_key = f"patron:{name}"

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
    existing_result = find_patron_by_name(conn, name)

    if isinstance(existing_result, Failure):
        error = existing_result.failure()
        return error_response("DATABASE_ERROR", f"Failed to check for existing patron: {error}")

    existing = existing_result.unwrap()
    if existing is not None:
        # Return existing patron (return_existing semantics)
        response_data = {
            "id": existing.id,
            "name": existing.name,
            "kind": existing.kind,
            "created_at": existing.created_at.isoformat(),
            "is_new": False,
        }
        # Store in idempotency cache if dedup_id provided
        if dedup_id is not None:
            _ = store_idempotency_key(
                conn, resource_key, "patron_register", dedup_id, {"data": response_data}
            )
        return success_response(response_data)

    # Create new patron
    now = datetime.now(UTC)
    patron_id = PatronId(str(uuid.uuid4()))

    patron = Patron(
        id=patron_id,
        name=name,
        kind=kind,
        created_at=now,
    )

    result = create_patron(conn, patron)

    if isinstance(result, Failure):
        error = result.failure()
        return error_response("DATABASE_ERROR", f"Failed to create patron: {error}")

    created = result.unwrap()
    response_data = {
        "id": created.id,
        "name": created.name,
        "kind": created.kind,
        "created_at": created.created_at.isoformat(),
        "is_new": True,
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
        Success envelope with patron details:
        {
            "ok": true,
            "data": {
                "id": "uuid-string",
                "name": "patron-name",
                "kind": "agent",
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
            "id": patron.id,
            "name": patron.name,
            "kind": patron.kind,
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
    table_id = TableId(str(uuid.uuid4()))

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


# @shell_complexity: 6 branches for table lookup + can_join guard + seat creation + dedup + error paths
# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
def table_join(
    table_id: str,
    patron_id: str,
) -> dict[str, Any]:
    """Join a discussion table by creating a seat.

    Creates a seat for the patron at the table and returns table details.

    Args:
        table_id: UUID of the table to join.
        patron_id: UUID of the patron joining the table.

    Returns:
        Success envelope with table details and seat info:
        {
            "ok": true,
            "data": {
                "table": {
                    "id": "uuid-string",
                    "question": "...",
                    "status": "open",
                    ...
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
        - NOT_FOUND: Table or patron not found
        - OPERATION_NOT_ALLOWED: Table is not open for joins (PAUSED or CLOSED)
        - DATABASE_ERROR: Failed to create seat
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

    # Check state machine guard: only OPEN tables can be joined
    if not can_join(table.status):
        return error_response(
            "OPERATION_NOT_ALLOWED",
            f"Cannot join table with status '{table.status.value}'. Only OPEN tables accept new joins.",
            {"table_status": table.status.value},
        )

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
        table_id=table_id,
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
            "seat": {
                "id": created_seat.id,
                "table_id": created_seat.table_id,
                "patron_id": created_seat.patron_id,
                "state": created_seat.state.value,
                "last_heartbeat": created_seat.last_heartbeat.isoformat(),
                "joined_at": created_seat.joined_at.isoformat(),
                "expires_at": expires_at.isoformat(),
            },
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


# @shell_complexity: 10 branches for table lookup + can_say guard + limits enforcement + dedup + mention resolution + error paths
# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
def table_say(
    table_id: str,
    content: str,
    speaker_name: str,
    patron_id: str | None = None,
    dedup_id: str | None = None,
) -> dict[str, Any]:
    """Append a saying (message) to a table.

    Args:
        table_id: UUID of the table.
        content: Markdown content of the saying.
        speaker_name: Name of the speaker.
        patron_id: Patron ID of the speaker (optional, recommended for agents).
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
                "created_at": "2024-01-01T00:00:00Z"
            }
        }

    Error codes:
        - NOT_FOUND: Table not found
        - OPERATION_NOT_ALLOWED: Table is closed (cannot add sayings)
        - LIMIT_EXCEEDED: Content limits exceeded (max_content_length, max_sayings_per_table, max_mentions_per_saying, max_bytes_per_table)
        - DATABASE_ERROR: Failed to append saying
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

    # Check state machine guard: CLOSED tables cannot have sayings added
    if not can_say(table.status):
        return error_response(
            "OPERATION_NOT_ALLOWED",
            f"Cannot add saying to table with status '{table.status.value}'. Table must be OPEN or PAUSED.",
            {"table_status": table.status.value},
        )

    # Create speaker
    if patron_id is not None:
        speaker = Speaker(
            kind=SpeakerKind.AGENT,
            name=speaker_name,
            patron_id=PatronId(patron_id),
        )
        speaker_key = patron_id
    else:
        speaker = Speaker(
            kind=SpeakerKind.HUMAN,
            name=speaker_name,
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

    # Log saying append
    log_say(
        logger,
        table_id=saying.table_id,
        sequence=saying.sequence,
        speaker_kind=saying.speaker.kind.value,
        speaker_name=saying.speaker.name,
        patron_id=saying.speaker.patron_id,
    )

    response_data = {
        "id": saying.id,
        "table_id": saying.table_id,
        "sequence": saying.sequence,
        "speaker": {
            "kind": saying.speaker.kind.value,
            "name": saying.speaker.name,
            "patron_id": saying.speaker.patron_id,
        },
        "content": saying.content,
        "pinned": saying.pinned,
        "created_at": saying.created_at.isoformat(),
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

    # Compute next_sequence
    if sayings:
        next_sequence = max(s.sequence for s in sayings) + 1
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
            f"Cannot perform control action on closed table. Closed is a terminal state.",
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
                f"Table version conflict during control operation",
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
            next_sequence = max(s.sequence for s in sayings) + 1

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

    # Timeout - return empty with current next_sequence
    max_seq_result = get_table_max_sequence(conn, table_id)
    if isinstance(max_seq_result, Failure):
        error = max_seq_result.failure()
        return error_response("DATABASE_ERROR", f"Failed to get max sequence: {error}")

    table_max_sequence = max_seq_result.unwrap()
    next_sequence = table_max_sequence + 1

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


# @shell_complexity: 6 branches for seat lookup + TTL update + state transition + expiry GC + error paths
# @invar:allow shell_result: MCP tools return serializable primitives, not Result
@mcp.tool
def seat_heartbeat(
    table_id: str,
    seat_id: str,
    dedup_id: str | None = None,
) -> dict[str, Any]:
    """Update a seat's heartbeat to indicate presence.

    Args:
        table_id: UUID of the table.
        seat_id: UUID of the seat to update.
        dedup_id: Optional explicit idempotency key for request deduplication.
            When provided, duplicate requests with the same dedup_id return
            the cached response (default TTL: 24 hours).
            Dedup scope is: {seat_id, dedup_id}.

    Returns:
        Success envelope with seat details and expiry:
        {
            "ok": true,
            "data": {
                "seat": {
                    "id": "uuid-string",
                    "table_id": "table-uuid",
                    "patron_id": "patron-uuid",
                    "state": "joined",
                    "last_heartbeat": "2024-01-01T00:00:00Z",
                    "joined_at": "2024-01-01T00:00:00Z"
                },
                "expires_at": "2024-01-01T00:01:00Z"
            }
        }

    Error codes:
        - NOT_FOUND: Seat not found
        - DATABASE_ERROR: Failed to update heartbeat
    """
    conn = next(get_mcp_db())

    # Resource key for idempotency scope: {seat_id}
    resource_key = f"seat:{seat_id}"

    # Check idempotency key if provided
    if dedup_id is not None:
        idempotency_result = check_idempotency_key(conn, resource_key, "seat_heartbeat", dedup_id)
        if isinstance(idempotency_result, Failure):
            error = idempotency_result.failure()
            return error_response("DATABASE_ERROR", f"Failed to check idempotency key: {error}")

        cached_response = idempotency_result.unwrap()
        if cached_response is not None:
            # Log dedup hit
            log_dedup_hit(logger, "seat_heartbeat", resource_key, dedup_id)
            # Return cached response (return_existing semantics)
            return success_response(cached_response["data"])

    now = datetime.now(UTC)

    result = repo_heartbeat_seat(conn, SeatId(seat_id), now)

    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, SeatNotFoundError):
            return error_response("NOT_FOUND", f"Seat not found: {seat_id}")
        return error_response("DATABASE_ERROR", f"Failed to update heartbeat: {error}")

    seat = result.unwrap()
    expires_at = calculate_expiry_time(seat.last_heartbeat, DEFAULT_SEAT_TTL_SECONDS)

    response_data = {
        "seat": {
            "id": seat.id,
            "table_id": seat.table_id,
            "patron_id": seat.patron_id,
            "state": seat.state.value,
            "last_heartbeat": seat.last_heartbeat.isoformat(),
            "joined_at": seat.joined_at.isoformat(),
        },
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
