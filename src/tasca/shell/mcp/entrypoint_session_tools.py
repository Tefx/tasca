"""Seat and proxy MCP tool implementations.

Extracted from entrypoints to keep top-level registration module focused on
public MCP surfaces while preserving response contracts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from returns.result import Failure

from tasca.core.domain.seat import SPEC_STATE_TO_INTERNAL, SeatId, SeatState
from tasca.core.services.seat_service import (
    DEFAULT_SEAT_TTL_SECONDS,
    calculate_expiry_time,
    filter_active_seats,
)
from tasca.shell.logging import get_logger, log_dedup_hit
from tasca.shell.mcp.database import get_mcp_db
from tasca.shell.mcp.responses import error_response, success_response
from tasca.shell.storage.idempotency_repo import check_idempotency_key, store_idempotency_key
from tasca.shell.storage.seat_repo import (
    SeatNotFoundError,
    find_seats_by_table,
    heartbeat_seat,
    heartbeat_seat_by_patron,
)

logger = get_logger(__name__)


# @invar:allow shell_result: Helper returns key tuple or MCP error payload for wrapper dispatch.
def _resolve_heartbeat_keys(
    table_id: str,
    patron_id: str | None,
    seat_id: str | None,
) -> tuple[str, str] | dict[str, Any]:
    """Build idempotency and lookup keys for seat heartbeat scope."""
    if patron_id is not None:
        return f"seat:{table_id}:{patron_id}", patron_id
    if seat_id is None:
        return error_response(
            "INVALID_REQUEST",
            "Either patron_id or seat_id must be provided",
        )
    return f"seat:{seat_id}", seat_id


# @invar:allow shell_result: Helper maps optional spec state to internal enum or MCP error payload.
def _parse_internal_state(
    state: Literal["running", "idle", "done"] | None,
) -> SeatState | None | dict[str, Any]:
    """Convert spec seat state into internal SeatState value."""
    if state is None:
        return None
    internal_state = SPEC_STATE_TO_INTERNAL.get(state)
    if internal_state is None:
        return error_response(
            "INVALID_REQUEST",
            f"Invalid state value: {state}. Must be 'running', 'idle', or 'done'",
        )
    return internal_state


# @invar:allow shell_result: Helper returns cached MCP response envelope, not Result[T, E].
def _load_cached_heartbeat(
    conn: Any,
    resource_key: str,
    dedup_id: str | None,
) -> dict[str, Any] | None:
    """Return idempotency-cached heartbeat response when available."""
    if dedup_id is None:
        return None
    idempotency_result = check_idempotency_key(conn, resource_key, "seat_heartbeat", dedup_id)
    if isinstance(idempotency_result, Failure):
        error = idempotency_result.failure()
        return error_response("DATABASE_ERROR", f"Failed to check idempotency key: {error}")

    cached_response = idempotency_result.unwrap()
    if cached_response is None:
        return None
    log_dedup_hit(logger, "seat_heartbeat", resource_key, dedup_id)
    cached_data = cached_response["data"]
    cached_data["_next_action"] = (
        "IMMEDIATELY call tasca.table_wait(since_sequence=...). "
        "Your response = tool_call, not text."
    )
    return success_response(cached_data)


# @shell_complexity: Boundary glue merges idempotency, legacy seat_id support, and MCP error envelopes.
# @invar:allow shell_result: MCP tool implementation returns protocol dict envelope, not Result[T, E].
def seat_heartbeat_impl(
    table_id: str,
    patron_id: str | None = None,
    state: Literal["running", "idle", "done"] | None = None,
    ttl_ms: int | None = None,
    dedup_id: str | None = None,
    seat_id: str | None = None,
) -> dict[str, Any]:
    """Heartbeat implementation shared by MCP entrypoint wrapper."""
    conn = next(get_mcp_db())

    key_result = _resolve_heartbeat_keys(table_id, patron_id, seat_id)
    if isinstance(key_result, dict):
        return key_result
    resource_key, lookup_key = key_result

    cached_result = _load_cached_heartbeat(conn, resource_key, dedup_id)
    if cached_result is not None:
        return cached_result

    now = datetime.now(UTC)
    ttl_seconds = int(ttl_ms / 1000) if ttl_ms is not None else DEFAULT_SEAT_TTL_SECONDS

    internal_state_result = _parse_internal_state(state)
    if isinstance(internal_state_result, dict):
        return internal_state_result
    internal_state = internal_state_result

    if patron_id is not None:
        result = heartbeat_seat_by_patron(conn, table_id, patron_id, now, internal_state)
    else:
        assert seat_id is not None
        result = heartbeat_seat(conn, SeatId(seat_id), now)

    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, SeatNotFoundError):
            return error_response("NOT_FOUND", f"Seat not found: {lookup_key}")
        return error_response("DATABASE_ERROR", f"Failed to update heartbeat: {error}")

    seat = result.unwrap()
    expires_at = calculate_expiry_time(seat.last_heartbeat, ttl_seconds)
    response_data: dict[str, Any] = {
        "expires_at": expires_at.isoformat(),
        "_next_action": (
            "IMMEDIATELY call tasca.table_wait(since_sequence=...). "
            "Your response = tool_call, not text."
        ),
    }
    if dedup_id is not None:
        store_idempotency_key(
            conn,
            resource_key,
            "seat_heartbeat",
            dedup_id,
            {"data": response_data},
            ttl_seconds=ttl_seconds,
            now=now,
        )
    return success_response(response_data)


# @invar:allow shell_result: MCP tool implementation returns protocol dict envelope, not Result[T, E].
def seat_list_impl(table_id: str, active_only: bool = True) -> dict[str, Any]:
    """Seat list implementation shared by MCP entrypoint wrapper."""
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


# @shell_complexity: Boundary glue branches by local/remote mode and preserves MCP error envelope shape.
# @invar:allow shell_result: MCP tool implementation returns protocol dict envelope, not Result[T, E].
async def connect_impl(url: str | None = None, token: str | None = None) -> dict[str, Any]:
    """Proxy mode switch implementation shared by MCP entrypoint wrapper."""
    if url is not None:
        from tasca.shell.mcp.proxy import switch_to_remote_with_session

        session_result = await switch_to_remote_with_session(url, token)
        if isinstance(session_result, Failure):
            err = session_result.failure()
            return error_response(
                "SESSION_INIT_FAILED",
                f"Failed to initialize MCP session with upstream: {err}",
                getattr(err, "details", {}),
            )
        config = session_result.unwrap()
    else:
        from tasca.shell.mcp.proxy import get_upstream_config, switch_to_local

        switch_to_local()
        config_result = get_upstream_config()
        if isinstance(config_result, Failure):
            err = config_result.failure()
            return error_response("CONFIG_ERROR", str(err))
        config = config_result.unwrap()

    mode = "remote" if config.is_remote else "local"
    return success_response(
        {
            "mode": mode,
            "url": config.url,
            "has_token": config.token is not None,
            "has_session": config.session_id is not None,
        }
    )


# @invar:allow shell_result: MCP tool implementation returns protocol dict envelope, not Result[T, E].
def connection_status_impl() -> dict[str, Any]:
    """Proxy status implementation shared by MCP entrypoint wrapper."""
    from tasca.shell.mcp.proxy import get_upstream_config

    config_result = get_upstream_config()
    if isinstance(config_result, Failure):
        err = config_result.failure()
        return error_response("CONFIG_ERROR", str(err))

    config = config_result.unwrap()
    mode = "remote" if config.is_remote else "local"
    is_healthy = True if not config.is_remote else config.url is not None and len(config.url) > 0
    return success_response(
        {
            "mode": mode,
            "url": config.url,
            "is_healthy": is_healthy,
        }
    )
