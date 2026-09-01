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

from pydantic import BaseModel, Field
from returns.result import Failure, Result, Success

from tasca.core.domain.seat import Seat, SeatId
from tasca.core.services.seat_service import (
    DEFAULT_SEAT_TTL_SECONDS,
    calculate_expiry_time,
    filter_active_seats,
)
from tasca.shell.api.deps import get_db
from tasca.shell.api.fastapi_compat import APIRouter, Depends, HTTPException, Query, status
from tasca.shell.storage.seat_repo import (
    SeatNotFoundError,
    find_seats_by_table,
)
from tasca.shell.storage.seat_repo import (
    heartbeat_seat as repo_heartbeat_seat,
)

if TYPE_CHECKING:
    pass

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
        active_count: Number of JOINED seats whose heartbeats are within TTL.
    """

    seats: list[Seat]
    active_count: int = Field(
        ..., description="Number of JOINED seats whose heartbeats are within TTL"
    )


# =============================================================================
# Helper Functions
# =============================================================================


# @shell_orchestration: Converts global settings to TTL value for use in routes
def _get_seat_ttl() -> Result[int, str]:
    """Get seat TTL from application settings."""
    # TODO: Make TTL configurable via settings
    return Success(DEFAULT_SEAT_TTL_SECONDS)


# @shell_orchestration: Database operation + HTTP error mapping
def _update_heartbeat(
    conn: sqlite3.Connection, seat_id: str, now: datetime
) -> Result[Seat, HTTPException]:
    """Update seat heartbeat.

    Raises:
        HTTPException: 404 if seat not found, 500 on database error.
    """
    result = repo_heartbeat_seat(conn, SeatId(seat_id), now)

    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, SeatNotFoundError):
            return Failure(HTTPException(status_code=404, detail=f"Seat not found: {seat_id}"))
        return Failure(HTTPException(status_code=500, detail=f"Failed to update heartbeat: {error}"))

    return Success(result.unwrap())


# @shell_orchestration: Database operation + HTTP error mapping
def _list_seats_for_table(
    conn: sqlite3.Connection, table_id: str
) -> Result[list[Seat], HTTPException]:
    """List all seats for a table.

    Raises:
        HTTPException: 500 on database error.
    """
    result = find_seats_by_table(conn, table_id)

    if isinstance(result, Failure):
        return Failure(HTTPException(status_code=500, detail=f"Failed to list seats: {result.failure()}"))

    return Success(result.unwrap())


def _heartbeat_response(
    conn: sqlite3.Connection,
    seat_id: str,
    now: datetime,
) -> Result[HeartbeatResponse, HTTPException]:
    """Update heartbeat and build the REST heartbeat response."""
    seat_result = _update_heartbeat(conn, seat_id, now)
    if isinstance(seat_result, Failure):
        return Failure(seat_result.failure())
    ttl_result = _get_seat_ttl()
    if isinstance(ttl_result, Failure):
        return Failure(HTTPException(status_code=500, detail=ttl_result.failure()))
    seat = seat_result.unwrap()
    expires_at = calculate_expiry_time(seat.last_heartbeat, ttl_result.unwrap())
    return Success(HeartbeatResponse(seat=seat, expires_at=expires_at))


def _seat_list_response(
    conn: sqlite3.Connection,
    table_id: str,
    active_only: bool,
    now: datetime,
) -> Result[SeatListResponse, HTTPException]:
    """List seats and compute the active seat count."""
    ttl_result = _get_seat_ttl()
    if isinstance(ttl_result, Failure):
        return Failure(HTTPException(status_code=500, detail=ttl_result.failure()))
    seats_result = _list_seats_for_table(conn, table_id)
    if isinstance(seats_result, Failure):
        return Failure(seats_result.failure())
    ttl = ttl_result.unwrap()
    all_seats = seats_result.unwrap()
    seats = filter_active_seats(all_seats, ttl, now) if active_only else all_seats
    active_count = len(filter_active_seats(all_seats, ttl, now))
    return Success(SeatListResponse(seats=seats, active_count=active_count))


# =============================================================================
# POST /tables/{table_id}/seats/{seat_id}/heartbeat - Update seat heartbeat
# =============================================================================


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
    result = _heartbeat_response(conn, seat_id, datetime.now(UTC))
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()


# =============================================================================
# GET /tables/{table_id}/seats - List seats for a table
# =============================================================================


@router.get("", response_model=SeatListResponse)
async def list_seats_endpoint(
    table_id: str,
    active_only: bool = Query(
        default=True,
        description="If true, return only JOINED seats within TTL; false returns all seats",
    ),
    conn: sqlite3.Connection = Depends(get_db),
) -> SeatListResponse:
    """List seats for a table, optionally filtering departed and expired seats."""
    result = _seat_list_response(conn, table_id, active_only, datetime.now(UTC))
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()
