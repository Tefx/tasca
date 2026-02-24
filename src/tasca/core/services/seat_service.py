"""
Seat service - core business logic for seat TTL and lifecycle.

This module provides pure TTL calculation and expiry logic for seats.
All functions are pure (no I/O) with @deal.pre/@deal.post contracts.
"""

from datetime import datetime, timedelta

import deal

from tasca.core.domain.seat import Seat, SeatId, SeatState


# =============================================================================
# TTL Constants
# =============================================================================

# Default TTL in seconds (5 minutes)
DEFAULT_SEAT_TTL_SECONDS = 300

# Maximum TTL to prevent datetime overflow with extreme values
# Ensure: max_datetime + timedelta(seconds=MAX_TTL_SECONDS) <= datetime.MAX
MAX_TTL_SECONDS = 10_000_000  # ~115 days

# Reasonable year range for seat timestamps (prevents overflow edge cases)
# Allow enough margin for TTL addition
_MIN_SAFE_YEAR = 2000
_MAX_SAFE_YEAR = 2100


# =============================================================================
# Contract Helpers
# =============================================================================


@deal.pre(lambda dt: dt is not None)
@deal.post(lambda result: isinstance(result, bool))
def _is_safe_datetime(dt: datetime) -> bool:
    """Check if datetime is within safe bounds for arithmetic operations.

    Prevents OverflowError when adding timedelta near datetime limits.
    """
    return _MIN_SAFE_YEAR <= dt.year <= _MAX_SAFE_YEAR


@deal.pre(lambda ttl_seconds: ttl_seconds is not None)
@deal.post(lambda result: isinstance(result, bool))
def _is_safe_ttl(ttl_seconds: int) -> bool:
    """Check if TTL is within safe bounds to prevent overflow.

    Ensures: datetime + timedelta(seconds=ttl) stays representable.
    """
    return 0 < ttl_seconds <= MAX_TTL_SECONDS


@deal.pre(lambda seat: seat is not None)
@deal.post(lambda result: isinstance(result, bool))
def _seat_has_safe_datetimes(seat: Seat) -> bool:
    """Check if seat's datetime fields are within safe bounds."""
    return _is_safe_datetime(seat.last_heartbeat) and _is_safe_datetime(seat.joined_at)


# =============================================================================
# TTL Calculations (Pure Logic)
# =============================================================================


@deal.pre(
    lambda seat, ttl_seconds, now: (
        seat is not None
        and ttl_seconds > 0
        and now is not None
        and _is_safe_datetime(now)
        and _is_safe_ttl(ttl_seconds)
        and _seat_has_safe_datetimes(seat)
    )
)
@deal.post(lambda result: isinstance(result, bool))
def is_seat_expired(seat: Seat, ttl_seconds: int, now: datetime) -> bool:
    """Check if a seat has expired based on TTL.

    A seat is expired when the time since its last heartbeat exceeds TTL.
    Only seats in JOINED state can be expired; LEFT seats are always
    considered non-expired for GC purposes (they're already gone).

    Args:
        seat: The seat to check.
        ttl_seconds: Time-to-live in seconds. Must be positive.
        now: Current timestamp.

    Returns:
        True if the seat's heartbeat is stale beyond TTL.
        False if seat is still active.

    Doctests:
        >>> from datetime import datetime
        >>> seat = Seat(
        ...     id=SeatId("test"), table_id="t1", patron_id="p1",
        ...     state=SeatState.JOINED,
        ...     last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
        ...     joined_at=datetime(2024, 1, 1, 12, 0, 0)
        ... )
        >>> # TTL of 60 seconds, 30 seconds passed - not expired
        >>> is_seat_expired(seat, 60, datetime(2024, 1, 1, 12, 0, 30))
        False
        >>> # TTL of 60 seconds, exactly 60 seconds - not expired (still within TTL)
        >>> is_seat_expired(seat, 60, datetime(2024, 1, 1, 12, 1, 0))
        False
        >>> # TTL of 60 seconds, 61 seconds passed - expired
        >>> is_seat_expired(seat, 60, datetime(2024, 1, 1, 12, 1, 1))
        True
        >>> # LEFT seats are never expired for GC purposes
        >>> seat_left = seat.model_copy(update={"state": SeatState.LEFT})
        >>> is_seat_expired(seat_left, 60, datetime(2024, 1, 1, 13, 0, 0))
        False
    """
    # LEFT seats are already departed, not candidates for expiry/GC
    if seat.state == SeatState.LEFT:
        return False

    expiry_time = calculate_expiry_time(seat.last_heartbeat, ttl_seconds)
    return now > expiry_time


@deal.pre(
    lambda last_heartbeat, ttl_seconds: (
        last_heartbeat is not None
        and ttl_seconds > 0
        and _is_safe_datetime(last_heartbeat)
        and _is_safe_ttl(ttl_seconds)
    )
)
@deal.post(lambda result: result is not None)
def calculate_expiry_time(last_heartbeat: datetime, ttl_seconds: int) -> datetime:
    """Calculate when a seat expires based on its last heartbeat.

    Args:
        last_heartbeat: The timestamp of the last heartbeat.
        ttl_seconds: Time-to-live in seconds. Must be positive.

    Returns:
        The datetime when the seat expires.

    Doctests:
        >>> from datetime import datetime
        >>> hb = datetime(2024, 1, 1, 12, 0, 0)
        >>> calculate_expiry_time(hb, 60)
        datetime.datetime(2024, 1, 1, 12, 1)
        >>> calculate_expiry_time(hb, 300)
        datetime.datetime(2024, 1, 1, 12, 5)
    """
    return last_heartbeat + timedelta(seconds=ttl_seconds)


@deal.pre(
    lambda seat, ttl_seconds, now: (
        seat is not None
        and ttl_seconds > 0
        and now is not None
        and _is_safe_datetime(now)
        and _is_safe_ttl(ttl_seconds)
        and _seat_has_safe_datetimes(seat)
    )
)
@deal.post(lambda result: result >= 0)
def seconds_until_expiry(seat: Seat, ttl_seconds: int, now: datetime) -> float:
    """Calculate seconds remaining before a seat expires.

    Args:
        seat: The seat to check.
        ttl_seconds: Time-to-live in seconds. Must be positive.
        now: Current timestamp.

    Returns:
        Seconds remaining before expiry. Returns 0 if already expired.
        For LEFT seats, returns 0 (already departed).

    Doctests:
        >>> from datetime import datetime
        >>> seat = Seat(
        ...     id=SeatId("test"), table_id="t1", patron_id="p1",
        ...     state=SeatState.JOINED,
        ...     last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
        ...     joined_at=datetime(2024, 1, 1, 12, 0, 0)
        ... )
        >>> seconds_until_expiry(seat, 60, datetime(2024, 1, 1, 12, 0, 30))
        30.0
        >>> # Already expired returns 0
        >>> seconds_until_expiry(seat, 60, datetime(2024, 1, 1, 12, 1, 30))
        0.0
        >>> # LEFT seats return 0
        >>> seat_left = seat.model_copy(update={"state": SeatState.LEFT})
        >>> seconds_until_expiry(seat_left, 60, datetime(2024, 1, 1, 12, 0, 30))
        0.0
    """
    if seat.state == SeatState.LEFT:
        return 0.0

    expiry_time = calculate_expiry_time(seat.last_heartbeat, ttl_seconds)
    remaining = (expiry_time - now).total_seconds()
    return max(0.0, remaining)


@deal.pre(
    lambda seats, ttl_seconds, now: (
        seats is not None
        and ttl_seconds > 0
        and now is not None
        and _is_safe_datetime(now)
        and _is_safe_ttl(ttl_seconds)
    )
)
@deal.post(lambda result: isinstance(result, list))
def filter_expired_seats(seats: list[Seat], ttl_seconds: int, now: datetime) -> list[Seat]:
    """Filter seats that have expired based on TTL.

    Args:
        seats: List of seats to filter.
        ttl_seconds: Time-to-live in seconds. Must be positive.
        now: Current timestamp.

    Returns:
        List of seats that have expired.

    Doctests:
        >>> from datetime import datetime
        >>> seats = [
        ...     Seat(
        ...         id=SeatId("active"), table_id="t1", patron_id="p1",
        ...         state=SeatState.JOINED,
        ...         last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
        ...         joined_at=datetime(2024, 1, 1, 12, 0, 0)
        ...     ),
        ...     Seat(
        ...         id=SeatId("expired"), table_id="t1", patron_id="p2",
        ...         state=SeatState.JOINED,
        ...         last_heartbeat=datetime(2024, 1, 1, 11, 0, 0),
        ...         joined_at=datetime(2024, 1, 1, 11, 0, 0)
        ...     ),
        ... ]
        >>> expired = filter_expired_seats(seats, 60, datetime(2024, 1, 1, 12, 1, 0))
        >>> len(expired)
        1
        >>> expired[0].id == SeatId("expired")
        True
    """
    return [seat for seat in seats if is_seat_expired(seat, ttl_seconds, now)]


@deal.pre(
    lambda seats, ttl_seconds, now: (
        seats is not None
        and ttl_seconds > 0
        and now is not None
        and _is_safe_datetime(now)
        and _is_safe_ttl(ttl_seconds)
    )
)
@deal.post(lambda result: isinstance(result, list))
def filter_active_seats(seats: list[Seat], ttl_seconds: int, now: datetime) -> list[Seat]:
    """Filter seats that are still active (not expired).

    Active seats are those with valid heartbeats and JOINED state.
    LEFT seats are also included (they explicitly left, not expired).

    Args:
        seats: List of seats to filter.
        ttl_seconds: Time-to-live in seconds. Must be positive.
        now: Current timestamp.

    Returns:
        List of seats that are still active.

    Doctests:
        >>> from datetime import datetime
        >>> seats = [
        ...     Seat(
        ...         id=SeatId("active"), table_id="t1", patron_id="p1",
        ...         state=SeatState.JOINED,
        ...         last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
        ...         joined_at=datetime(2024, 1, 1, 12, 0, 0)
        ...     ),
        ...     Seat(
        ...         id=SeatId("expired"), table_id="t1", patron_id="p2",
        ...         state=SeatState.JOINED,
        ...         last_heartbeat=datetime(2024, 1, 1, 11, 0, 0),
        ...         joined_at=datetime(2024, 1, 1, 11, 0, 0)
        ...     ),
        ... ]
        >>> active = filter_active_seats(seats, 60, datetime(2024, 1, 1, 12, 1, 0))
        >>> len(active)
        1
        >>> active[0].id == SeatId("active")
        True
    """
    return [seat for seat in seats if not is_seat_expired(seat, ttl_seconds, now)]


@deal.pre(lambda seat, now: seat is not None and now is not None)
@deal.post(lambda result: isinstance(result, datetime))
def heartbeat_update_time(seat: Seat, now: datetime) -> datetime:
    """Get the updated last_heartbeat time for a seat.

    This is a pure function that returns the timestamp to use for
    an updated heartbeat. It simply returns 'now' but provides
    a named operation for clarity.

    Args:
        seat: The seat to update (not modified).
        now: Current timestamp.

    Returns:
        The new last_heartbeat timestamp (same as 'now').

    Doctests:
        >>> from datetime import datetime
        >>> seat = Seat(
        ...     id=SeatId("test"), table_id="t1", patron_id="p1",
        ...     state=SeatState.JOINED,
        ...     last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
        ...     joined_at=datetime(2024, 1, 1, 12, 0, 0)
        ... )
        >>> new_time = heartbeat_update_time(seat, datetime(2024, 1, 1, 12, 5, 0))
        >>> new_time
        datetime.datetime(2024, 1, 1, 12, 5)
    """
    return now


@deal.pre(lambda seat, now: seat is not None and now is not None)
@deal.post(lambda result: result.last_heartbeat is not None)
def create_heartbeat_update(seat: Seat, now: datetime) -> Seat:
    """Create a new Seat with updated last_heartbeat.

    This is a pure function that creates a new Seat model with
    the last_heartbeat updated to now. Does not modify the original.

    Args:
        seat: The original seat.
        now: Current timestamp for the new heartbeat.

    Returns:
        New Seat with updated last_heartbeat.

    Doctests:
        >>> from datetime import datetime
        >>> seat = Seat(
        ...     id=SeatId("test"), table_id="t1", patron_id="p1",
        ...     state=SeatState.JOINED,
        ...     last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
        ...     joined_at=datetime(2024, 1, 1, 12, 0, 0)
        ... )
        >>> updated = create_heartbeat_update(seat, datetime(2024, 1, 1, 12, 5, 0))
        >>> updated.last_heartbeat
        datetime.datetime(2024, 1, 1, 12, 5)
        >>> # Original is unchanged
        >>> seat.last_heartbeat
        datetime.datetime(2024, 1, 1, 12, 0)
    """
    return seat.model_copy(update={"last_heartbeat": now})
