"""
Seats API routes.

Endpoints for seat and presence management:
- POST /tables/{table_id}/seats/{seat_id}/heartbeat - Update seat heartbeat
- GET /tables/{table_id}/seats - List seats for a table
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from returns.result import Failure, Success

from tasca.core.domain.seat import Seat, SeatId
from tasca.core.services.seat_service import (
    DEFAULT_SEAT_TTL_SECONDS,
    calculate_expiry_time,
    filter_active_seats,
)
from tasca.shell.api.deps import get_db
from tasca.shell.storage.seat_repo import (
    SeatNotFoundError,
    find_seats_by_table,
    heartbeat_seat as repo_heartbeat_seat,
)

if TYPE_CHECKING:
    from collections.abc import Generator

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================


class HeartbeatResponse(BaseModel):
    """Response model for seat heartbeat endpoint.

    Attributes:
        seat: The updated seat with new last_heartbeat.
        expires_at: When the seat will expire if no further heartbeat.
    """

    seat: Seat
    expires_at: datetime = Field(..., description="When the seat expires if no heartbeat")


class SeatListResponse(BaseModel):
    """Response model for listing seats.

    Attributes:
        seats: List of seats for the table.
        active_count: Number of active (non-expired) seats.
    """

    seats: list[Seat]
    active_count: int = Field(..., description="Number of active (non-expired) seats")


# =============================================================================
# Helper Functions
# =============================================================================


# @invar:allow shell_result: Thin adapter - retrieves TTL from settings (env vars)
# @shell_orchestration: Converts global settings to TTL value for use in routes
def _get_seat_ttl() -> int:
    """Get seat TTL from application settings."""
    # TODO: Make TTL configurable via settings
    return DEFAULT_SEAT_TTL_SECONDS


# @invar:allow shell_result: Helper raises HTTPException directly (no Result needed)
# @shell_orchestration: Database operation + HTTP error mapping
def _update_heartbeat(conn: sqlite3.Connection, seat_id: str, now: datetime) -> Seat:
    """Update seat heartbeat.

    Raises:
        HTTPException: 404 if seat not found, 500 on database error.
    """
    result = repo_heartbeat_seat(conn, SeatId(seat_id), now)

    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, SeatNotFoundError):
            raise HTTPException(status_code=404, detail=f"Seat not found: {seat_id}")
        raise HTTPException(status_code=500, detail=f"Failed to update heartbeat: {error}")

    return result.unwrap()


# @invar:allow shell_result: Helper raises HTTPException directly (no Result needed)
# @shell_orchestration: Database operation + HTTP error mapping
def _list_seats_for_table(conn: sqlite3.Connection, table_id: str) -> list[Seat]:
    """List all seats for a table.

    Raises:
        HTTPException: 500 on database error.
    """
    result = find_seats_by_table(conn, table_id)

    if isinstance(result, Failure):
        raise HTTPException(status_code=500, detail=f"Failed to list seats: {result.failure()}")

    return result.unwrap()


# =============================================================================
# POST /tables/{table_id}/seats/{seat_id}/heartbeat - Update seat heartbeat
# =============================================================================


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
@router.post(
    "/{seat_id}/heartbeat",
    response_model=HeartbeatResponse,
    status_code=status.HTTP_200_OK,
)
async def heartbeat_seat_endpoint(
    table_id: str,
    seat_id: str,
    conn: sqlite3.Connection = Depends(get_db),
) -> HeartbeatResponse:
    """Update a seat's heartbeat. Returns updated seat with expires_at."""
    now = datetime.now(UTC)
    seat = _update_heartbeat(conn, seat_id, now)
    expires_at = calculate_expiry_time(seat.last_heartbeat, _get_seat_ttl())
    return HeartbeatResponse(seat=seat, expires_at=expires_at)


# =============================================================================
# GET /tables/{table_id}/seats - List seats for a table
# =============================================================================


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
@router.get("", response_model=SeatListResponse)
async def list_seats_endpoint(
    table_id: str,
    active_only: bool = Query(default=True, description="Exclude expired seats"),
    conn: sqlite3.Connection = Depends(get_db),
) -> SeatListResponse:
    """List seats for a table, optionally filtering expired seats."""
    now = datetime.now(UTC)
    ttl = _get_seat_ttl()
    seats = _list_seats_for_table(conn, table_id)

    if active_only:
        seats = filter_active_seats(seats, ttl, now)

    all_seats = _list_seats_for_table(conn, table_id)
    active_count = len(filter_active_seats(all_seats, ttl, now))

    return SeatListResponse(seats=seats, active_count=active_count)
