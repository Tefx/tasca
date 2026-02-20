"""
Unit tests for seat TTL and garbage collection.

Tests for:
- Core: TTL calculation and expiry logic
- Shell: Heartbeat updates and GC operations
"""

import sqlite3
from datetime import datetime, timedelta

import pytest
from returns.result import Failure, Success

from tasca.core.domain.seat import Seat, SeatId, SeatState
from tasca.core.services.seat_service import (
    calculate_expiry_time,
    create_heartbeat_update,
    filter_active_seats,
    filter_expired_seats,
    heartbeat_update_time,
    is_seat_expired,
    seconds_until_expiry,
)
from tasca.shell.storage.seat_repo import (
    count_active_seats,
    create_seat,
    create_seats_table,
    delete_seat,
    delete_seats,
    find_expired_seats,
    find_seats_by_table,
    gc_expired_seats,
    get_seat,
    heartbeat_seat,
    SeatDatabaseError,
    SeatNotFoundError,
)


# =============================================================================
# Core TTL Logic Tests
# =============================================================================


class TestIsSeatExpired:
    """Tests for is_seat_expired function."""

    def test_not_expired_within_ttl(self) -> None:
        """Seat is not expired when within TTL."""
        seat = Seat(
            id=SeatId("test"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        # 30 seconds after heartbeat, TTL is 60 seconds
        assert is_seat_expired(seat, 60, datetime(2024, 1, 1, 12, 0, 30)) is False

    def test_not_expired_at_ttl_boundary(self) -> None:
        """Seat is not expired exactly at TTL boundary."""
        seat = Seat(
            id=SeatId("test"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        # Exactly at TTL boundary (60 seconds)
        assert is_seat_expired(seat, 60, datetime(2024, 1, 1, 12, 1, 0)) is False

    def test_expired_after_ttl(self) -> None:
        """Seat is expired after TTL."""
        seat = Seat(
            id=SeatId("test"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        # 61 seconds after heartbeat, TTL is 60 seconds
        assert is_seat_expired(seat, 60, datetime(2024, 1, 1, 12, 1, 1)) is True

    def test_left_seat_never_expired(self) -> None:
        """LEFT seats are never considered expired."""
        seat = Seat(
            id=SeatId("test"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.LEFT,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        # Even 1 hour after heartbeat
        assert is_seat_expired(seat, 60, datetime(2024, 1, 1, 13, 0, 0)) is False

    def test_different_ttl_values(self) -> None:
        """Different TTL values affect expiry correctly."""
        seat = Seat(
            id=SeatId("test"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        now = datetime(2024, 1, 1, 12, 5, 0)  # 5 minutes later

        # With 1 minute TTL, expired
        assert is_seat_expired(seat, 60, now) is True

        # With 10 minute TTL, not expired
        assert is_seat_expired(seat, 600, now) is False


class TestCalculateExpiryTime:
    """Tests for calculate_expiry_time function."""

    def test_basic_calculation(self) -> None:
        """Expiry time is heartbeat + TTL."""
        heartbeat = datetime(2024, 1, 1, 12, 0, 0)
        expiry = calculate_expiry_time(heartbeat, 60)
        assert expiry == datetime(2024, 1, 1, 12, 1, 0)

    def test_large_ttl(self) -> None:
        """Large TTL values work correctly."""
        heartbeat = datetime(2024, 1, 1, 12, 0, 0)
        expiry = calculate_expiry_time(heartbeat, 3600)  # 1 hour
        assert expiry == datetime(2024, 1, 1, 13, 0, 0)

    def test_default_ttl(self) -> None:
        """Default TTL (5 minutes) works correctly."""
        from tasca.core.services.seat_service import DEFAULT_SEAT_TTL_SECONDS

        heartbeat = datetime(2024, 1, 1, 12, 0, 0)
        expiry = calculate_expiry_time(heartbeat, DEFAULT_SEAT_TTL_SECONDS)
        assert expiry == datetime(2024, 1, 1, 12, 5, 0)


class TestSecondsUntilExpiry:
    """Tests for seconds_until_expiry function."""

    def test_seconds_remaining(self) -> None:
        """Seconds remaining is calculated correctly."""
        seat = Seat(
            id=SeatId("test"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        # 30 seconds into a 60 second TTL
        remaining = seconds_until_expiry(seat, 60, datetime(2024, 1, 1, 12, 0, 30))
        assert remaining == 30.0

    def test_already_expired(self) -> None:
        """Expired seats return 0 seconds."""
        seat = Seat(
            id=SeatId("test"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        # 2 minutes after a 60 second TTL
        remaining = seconds_until_expiry(seat, 60, datetime(2024, 1, 1, 12, 2, 0))
        assert remaining == 0.0

    def test_left_seat_returns_zero(self) -> None:
        """LEFT seats return 0 seconds (already departed)."""
        seat = Seat(
            id=SeatId("test"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.LEFT,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        remaining = seconds_until_expiry(seat, 60, datetime(2024, 1, 1, 12, 0, 30))
        assert remaining == 0.0


class TestFilterExpiredSeats:
    """Tests for filter_expired_seats function."""

    def test_filter_expired(self) -> None:
        """Expired seats are filtered correctly."""
        seats = [
            Seat(
                id=SeatId("active"),
                table_id="t1",
                patron_id="p1",
                state=SeatState.JOINED,
                last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
                joined_at=datetime(2024, 1, 1, 12, 0, 0),
            ),
            Seat(
                id=SeatId("expired"),
                table_id="t1",
                patron_id="p2",
                state=SeatState.JOINED,
                last_heartbeat=datetime(2024, 1, 1, 11, 0, 0),  # 1 hour ago
                joined_at=datetime(2024, 1, 1, 11, 0, 0),
            ),
        ]
        now = datetime(2024, 1, 1, 12, 1, 0)
        expired = filter_expired_seats(seats, 60, now)
        assert len(expired) == 1
        assert expired[0].id == SeatId("expired")

    def test_filter_empty_list(self) -> None:
        """Empty list returns empty list."""
        expired = filter_expired_seats([], 60, datetime(2024, 1, 1, 12, 0, 0))
        assert expired == []

    def test_filter_no_expired(self) -> None:
        """All active seats return empty list."""
        seats = [
            Seat(
                id=SeatId("active1"),
                table_id="t1",
                patron_id="p1",
                state=SeatState.JOINED,
                last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
                joined_at=datetime(2024, 1, 1, 12, 0, 0),
            ),
            Seat(
                id=SeatId("active2"),
                table_id="t1",
                patron_id="p2",
                state=SeatState.JOINED,
                last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
                joined_at=datetime(2024, 1, 1, 12, 0, 0),
            ),
        ]
        now = datetime(2024, 1, 1, 12, 0, 30)
        expired = filter_expired_seats(seats, 60, now)
        assert expired == []


class TestFilterActiveSeats:
    """Tests for filter_active_seats function."""

    def test_filter_active(self) -> None:
        """Active seats are filtered correctly."""
        seats = [
            Seat(
                id=SeatId("active"),
                table_id="t1",
                patron_id="p1",
                state=SeatState.JOINED,
                last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
                joined_at=datetime(2024, 1, 1, 12, 0, 0),
            ),
            Seat(
                id=SeatId("expired"),
                table_id="t1",
                patron_id="p2",
                state=SeatState.JOINED,
                last_heartbeat=datetime(2024, 1, 1, 11, 0, 0),
                joined_at=datetime(2024, 1, 1, 11, 0, 0),
            ),
        ]
        now = datetime(2024, 1, 1, 12, 1, 0)
        active = filter_active_seats(seats, 60, now)
        assert len(active) == 1
        assert active[0].id == SeatId("active")

    def test_filter_includes_left_seats(self) -> None:
        """LEFT seats are included as active (not expired)."""
        seats = [
            Seat(
                id=SeatId("left"),
                table_id="t1",
                patron_id="p1",
                state=SeatState.LEFT,
                last_heartbeat=datetime(2024, 1, 1, 11, 0, 0),  # old heartbeat
                joined_at=datetime(2024, 1, 1, 11, 0, 0),
            ),
        ]
        now = datetime(2024, 1, 1, 12, 1, 0)  # 1 hour later
        active = filter_active_seats(seats, 60, now)
        assert len(active) == 1
        assert active[0].state == SeatState.LEFT


class TestHeartbeatUpdate:
    """Tests for heartbeat update functions."""

    def test_heartbeat_update_time(self) -> None:
        """heartbeat_update_time returns the passed timestamp."""
        seat = Seat(
            id=SeatId("test"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        new_time = datetime(2024, 1, 1, 12, 5, 0)
        result = heartbeat_update_time(seat, new_time)
        assert result == new_time

    def test_create_heartbeat_update(self) -> None:
        """create_heartbeat_update creates new seat with updated heartbeat."""
        seat = Seat(
            id=SeatId("test"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        new_time = datetime(2024, 1, 1, 12, 5, 0)
        updated = create_heartbeat_update(seat, new_time)
        assert updated.last_heartbeat == new_time
        assert updated.id == seat.id
        assert updated.table_id == seat.table_id
        assert updated.patron_id == seat.patron_id
        assert updated.state == seat.state
        # Original is unchanged
        assert seat.last_heartbeat == datetime(2024, 1, 1, 12, 0, 0)


# =============================================================================
# Shell Repository Tests
# =============================================================================


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """Create an in-memory database with seats table."""
    conn = sqlite3.connect(":memory:")
    result = create_seats_table(conn)
    assert isinstance(result, Success)
    yield conn
    conn.close()


class TestCreateSeat:
    """Tests for create_seat function."""

    def test_create_seat_success(self, db_conn: sqlite3.Connection) -> None:
        """Creating a seat succeeds."""
        seat = Seat(
            id=SeatId("s1"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        result = create_seat(db_conn, seat)
        assert isinstance(result, Success)
        assert result.unwrap() == seat

    def test_create_seat_duplicate_fails(self, db_conn: sqlite3.Connection) -> None:
        """Creating duplicate seat fails."""
        seat = Seat(
            id=SeatId("s1"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        result1 = create_seat(db_conn, seat)
        assert isinstance(result1, Success)

        result2 = create_seat(db_conn, seat)
        assert isinstance(result2, Failure)


class TestGetSeat:
    """Tests for get_seat function."""

    def test_get_seat_success(self, db_conn: sqlite3.Connection) -> None:
        """Getting an existing seat succeeds."""
        seat = Seat(
            id=SeatId("s1"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        create_seat(db_conn, seat)

        result = get_seat(db_conn, SeatId("s1"))
        assert isinstance(result, Success)
        retrieved = result.unwrap()
        assert retrieved.id == seat.id
        assert retrieved.table_id == seat.table_id

    def test_get_seat_not_found(self, db_conn: sqlite3.Connection) -> None:
        """Getting non-existent seat fails with SeatNotFoundError."""
        result = get_seat(db_conn, SeatId("nonexistent"))
        assert isinstance(result, Failure)
        assert isinstance(result.failure(), SeatNotFoundError)


class TestHeartbeatSeat:
    """Tests for heartbeat_seat function."""

    def test_heartbeat_updates_timestamp(self, db_conn: sqlite3.Connection) -> None:
        """Heartbeat updates last_heartbeat timestamp."""
        seat = Seat(
            id=SeatId("s1"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        create_seat(db_conn, seat)

        new_time = datetime(2024, 1, 1, 12, 5, 0)
        result = heartbeat_seat(db_conn, SeatId("s1"), new_time)
        assert isinstance(result, Success)
        assert result.unwrap().last_heartbeat == new_time

        # Verify persistence
        get_result = get_seat(db_conn, SeatId("s1"))
        assert isinstance(get_result, Success)
        assert get_result.unwrap().last_heartbeat == new_time

    def test_heartbeat_nonexistent_seat_fails(self, db_conn: sqlite3.Connection) -> None:
        """Heartbeat on non-existent seat fails."""
        result = heartbeat_seat(db_conn, SeatId("nonexistent"), datetime(2024, 1, 1))
        assert isinstance(result, Failure)
        assert isinstance(result.failure(), SeatNotFoundError)


class TestFindExpiredSeats:
    """Tests for find_expired_seats function."""

    def test_find_expired_seats(self, db_conn: sqlite3.Connection) -> None:
        """Finding expired seats works correctly."""
        # Create seats with different heartbeat times
        old_seat = Seat(
            id=SeatId("old"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 11, 0, 0),  # 1 hour ago
            joined_at=datetime(2024, 1, 1, 11, 0, 0),
        )
        new_seat = Seat(
            id=SeatId("new"),
            table_id="t1",
            patron_id="p2",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),  # current
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        create_seat(db_conn, old_seat)
        create_seat(db_conn, new_seat)

        # Find expired with 60 second TTL
        result = find_expired_seats(db_conn, 60, datetime(2024, 1, 1, 12, 1, 0))
        assert isinstance(result, Success)
        expired = result.unwrap()
        assert len(expired) == 1
        assert expired[0].id == SeatId("old")

    def test_find_expired_excludes_left_seats(self, db_conn: sqlite3.Connection) -> None:
        """LEFT seats are not returned as expired."""
        left_seat = Seat(
            id=SeatId("left"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.LEFT,
            last_heartbeat=datetime(2024, 1, 1, 11, 0, 0),  # old
            joined_at=datetime(2024, 1, 1, 11, 0, 0),
        )
        create_seat(db_conn, left_seat)

        result = find_expired_seats(db_conn, 60, datetime(2024, 1, 1, 12, 1, 0))
        assert isinstance(result, Success)
        assert len(result.unwrap()) == 0


class TestGcExpiredSeats:
    """Tests for gc_expired_seats function."""

    def test_gc_deletes_expired_seats(self, db_conn: sqlite3.Connection) -> None:
        """GC deletes expired seats."""
        old_seat = Seat(
            id=SeatId("old"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 11, 0, 0),
            joined_at=datetime(2024, 1, 1, 11, 0, 0),
        )
        new_seat = Seat(
            id=SeatId("new"),
            table_id="t1",
            patron_id="p2",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        create_seat(db_conn, old_seat)
        create_seat(db_conn, new_seat)

        result = gc_expired_seats(db_conn, 60, datetime(2024, 1, 1, 12, 1, 0))
        assert isinstance(result, Success)
        assert result.unwrap() == 1

        # Verify old_seat is deleted
        get_result = get_seat(db_conn, SeatId("old"))
        assert isinstance(get_result, Failure)

        # Verify new_seat still exists
        get_result = get_seat(db_conn, SeatId("new"))
        assert isinstance(get_result, Success)

    def test_gc_no_expired_seats(self, db_conn: sqlite3.Connection) -> None:
        """GC with no expired seats returns 0."""
        result = gc_expired_seats(db_conn, 60, datetime(2024, 1, 1, 12, 1, 0))
        assert isinstance(result, Success)
        assert result.unwrap() == 0


class TestDeleteSeat:
    """Tests for delete_seat function."""

    def test_delete_existing_seat(self, db_conn: sqlite3.Connection) -> None:
        """Deleting an existing seat succeeds."""
        seat = Seat(
            id=SeatId("s1"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        create_seat(db_conn, seat)

        result = delete_seat(db_conn, SeatId("s1"))
        assert isinstance(result, Success)

        # Verify deleted
        get_result = get_seat(db_conn, SeatId("s1"))
        assert isinstance(get_result, Failure)

    def test_delete_nonexistent_seat_fails(self, db_conn: sqlite3.Connection) -> None:
        """Deleting non-existent seat fails."""
        result = delete_seat(db_conn, SeatId("nonexistent"))
        assert isinstance(result, Failure)


class TestDeleteSeats:
    """Tests for delete_seats function."""

    def test_delete_multiple_seats(self, db_conn: sqlite3.Connection) -> None:
        """Deleting multiple seats succeeds."""
        for i in range(3):
            seat = Seat(
                id=SeatId(f"s{i}"),
                table_id="t1",
                patron_id=f"p{i}",
                state=SeatState.JOINED,
                last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
                joined_at=datetime(2024, 1, 1, 12, 0, 0),
            )
            create_seat(db_conn, seat)

        result = delete_seats(db_conn, [SeatId("s0"), SeatId("s1")])
        assert isinstance(result, Success)
        assert result.unwrap() == 2

    def test_delete_empty_list(self, db_conn: sqlite3.Connection) -> None:
        """Deleting empty list returns 0."""
        result = delete_seats(db_conn, [])
        assert isinstance(result, Success)
        assert result.unwrap() == 0


class TestFindSeatsByTable:
    """Tests for find_seats_by_table function."""

    def test_find_seats_by_table(self, db_conn: sqlite3.Connection) -> None:
        """Finding seats by table works."""
        seat1 = Seat(
            id=SeatId("s1"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        seat2 = Seat(
            id=SeatId("s2"),
            table_id="t1",
            patron_id="p2",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        seat3 = Seat(
            id=SeatId("s3"),
            table_id="t2",
            patron_id="p3",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        create_seat(db_conn, seat1)
        create_seat(db_conn, seat2)
        create_seat(db_conn, seat3)

        result = find_seats_by_table(db_conn, "t1")
        assert isinstance(result, Success)
        seats = result.unwrap()
        assert len(seats) == 2

        result = find_seats_by_table(db_conn, "t2")
        assert isinstance(result, Success)
        assert len(result.unwrap()) == 1

        result = find_seats_by_table(db_conn, "nonexistent")
        assert isinstance(result, Success)
        assert len(result.unwrap()) == 0


class TestCountActiveSeats:
    """Tests for count_active_seats function."""

    def test_count_active_seats(self, db_conn: sqlite3.Connection) -> None:
        """Counting active seats works."""
        # Active seat
        seat1 = Seat(
            id=SeatId("active"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 12, 0, 0),
            joined_at=datetime(2024, 1, 1, 12, 0, 0),
        )
        # Expired seat
        seat2 = Seat(
            id=SeatId("expired"),
            table_id="t1",
            patron_id="p2",
            state=SeatState.JOINED,
            last_heartbeat=datetime(2024, 1, 1, 11, 0, 0),
            joined_at=datetime(2024, 1, 1, 11, 0, 0),
        )
        create_seat(db_conn, seat1)
        create_seat(db_conn, seat2)

        result = count_active_seats(db_conn, "t1", 60, datetime(2024, 1, 1, 12, 1, 0))
        assert isinstance(result, Success)
        assert result.unwrap() == 1  # Only seat1 is active


# =============================================================================
# Integration Tests (Core + Shell)
# =============================================================================


class TestTTLIntegration:
    """Integration tests for TTL functionality."""

    def test_full_lifecycle(self, db_conn: sqlite3.Connection) -> None:
        """Test full seat lifecycle: create -> heartbeat -> expiry -> GC."""
        now = datetime(2024, 1, 1, 12, 0, 0)
        ttl = 60  # 60 seconds

        # Create seat
        seat = Seat(
            id=SeatId("s1"),
            table_id="t1",
            patron_id="p1",
            state=SeatState.JOINED,
            last_heartbeat=now,
            joined_at=now,
        )
        result = create_seat(db_conn, seat)
        assert isinstance(result, Success)

        # Initially not expired
        get_result = get_seat(db_conn, SeatId("s1"))
        assert isinstance(get_result, Success)
        retrieved = get_result.unwrap()
        assert is_seat_expired(retrieved, ttl, now) is False

        # Heartbeat after 30 seconds
        later = now + timedelta(seconds=30)
        hb_result = heartbeat_seat(db_conn, SeatId("s1"), later)
        assert isinstance(hb_result, Success)

        # Still not expired
        get_result = get_seat(db_conn, SeatId("s1"))
        assert isinstance(get_result, Success)
        retrieved = get_result.unwrap()
        assert is_seat_expired(retrieved, ttl, later + timedelta(seconds=30)) is False

        # Now check expiry after TTL passes
        expiry_time = later + timedelta(seconds=ttl + 1)
        get_result = get_seat(db_conn, SeatId("s1"))
        assert isinstance(get_result, Success)
        retrieved = get_result.unwrap()
        assert is_seat_expired(retrieved, ttl, expiry_time) is True

        # Run GC
        gc_result = gc_expired_seats(db_conn, ttl, expiry_time)
        assert isinstance(gc_result, Success)
        assert gc_result.unwrap() == 1

        # Seat should be gone
        get_result = get_seat(db_conn, SeatId("s1"))
        assert isinstance(get_result, Failure)
