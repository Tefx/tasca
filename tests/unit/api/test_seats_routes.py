"""
Unit tests for seats API routes.

Uses FastAPI TestClient with an in-memory SQLite database.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tasca.core.domain.seat import Seat, SeatId, SeatState
from tasca.core.domain.table import Table, TableId, TableStatus, Version
from tasca.core.services.seat_service import DEFAULT_SEAT_TTL_SECONDS
from tasca.shell.api.routes.seats import router
from tasca.shell.storage.database import apply_schema
from tasca.shell.storage.seat_repo import create_seat, create_seats_table
from tasca.shell.storage.table_repo import create_table


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def test_db() -> Generator[sqlite3.Connection, None, None]:
    """Create an in-memory database with seats schema."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    apply_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def app(test_db: sqlite3.Connection) -> FastAPI:
    """Create a FastAPI app with seats router and test database."""
    app = FastAPI()

    # Override the get_db dependency to use test database
    def get_test_db() -> Generator[sqlite3.Connection, None, None]:
        yield test_db

    from tasca.shell.api.deps import get_db

    app.dependency_overrides[get_db] = get_test_db

    app.include_router(router, prefix="/tables/{table_id}/seats")
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)


# =============================================================================
# Helper Functions
# =============================================================================


def create_test_table(conn: sqlite3.Connection, table_id: str = "test-table-1") -> None:
    """Create a test table directly in the database."""
    now = datetime.now(UTC)
    table = Table(
        id=TableId(table_id),
        question="Test question?",
        context="Test context",
        status=TableStatus.OPEN,
        version=Version(1),
        created_at=now,
        updated_at=now,
    )
    create_table(conn, table)


def create_test_seat(
    conn: sqlite3.Connection,
    seat_id: str = "test-seat-1",
    table_id: str = "test-table-1",
    patron_id: str = "test-patron-1",
    state: SeatState = SeatState.JOINED,
    last_heartbeat: datetime | None = None,
) -> Seat:
    """Create a test seat directly in the database."""
    now = datetime.now(UTC)
    seat = Seat(
        id=SeatId(seat_id),
        table_id=table_id,
        patron_id=patron_id,
        state=state,
        last_heartbeat=last_heartbeat or now,
        joined_at=now,
    )
    result = create_seat(conn, seat)
    return result.unwrap()


# =============================================================================
# POST /tables/{table_id}/seats/{seat_id}/heartbeat - Heartbeat Tests
# =============================================================================


class TestHeartbeatSeat:
    """Tests for POST /tables/{table_id}/seats/{seat_id}/heartbeat endpoint."""

    def test_heartbeat_seat_success(self, client: TestClient, test_db: sqlite3.Connection) -> None:
        """Heartbeat updates last_heartbeat and returns expires_at."""
        # Setup
        create_test_table(test_db, "table-1")
        now = datetime.now(UTC)
        seat = create_test_seat(
            test_db,
            seat_id="seat-1",
            table_id="table-1",
            last_heartbeat=now - timedelta(minutes=2),  # 2 minutes ago
        )

        # Execute
        response = client.post("/tables/table-1/seats/seat-1/heartbeat")

        # Verify
        assert response.status_code == 200
        data = response.json()
        assert data["seat"]["id"] == "seat-1"
        assert data["seat"]["table_id"] == "table-1"
        assert "expires_at" in data

        # expires_at should be TTL seconds from now
        expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
        expected_expiry = datetime.now(UTC) + timedelta(seconds=DEFAULT_SEAT_TTL_SECONDS)
        # Allow 5 second tolerance for test execution time
        assert abs((expires_at - expected_expiry).total_seconds()) < 5

    def test_heartbeat_seat_not_found(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Heartbeat for non-existent seat returns 404."""
        # Setup - no seat created
        create_test_table(test_db, "table-1")

        # Execute
        response = client.post("/tables/table-1/seats/nonexistent-seat/heartbeat")

        # Verify
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_heartbeat_updates_last_heartbeat(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Each heartbeat call updates the last_heartbeat timestamp."""
        # Setup
        create_test_table(test_db, "table-1")
        old_time = datetime.now(UTC) - timedelta(minutes=5)
        seat = create_test_seat(
            test_db,
            seat_id="seat-1",
            table_id="table-1",
            last_heartbeat=old_time,
        )

        # First heartbeat
        response1 = client.post("/tables/table-1/seats/seat-1/heartbeat")
        assert response1.status_code == 200
        last_heartbeat_1 = datetime.fromisoformat(
            response1.json()["seat"]["last_heartbeat"].replace("Z", "+00:00")
        )

        # Second heartbeat (slightly later)
        import time

        time.sleep(0.1)
        response2 = client.post("/tables/table-1/seats/seat-1/heartbeat")
        assert response2.status_code == 200
        last_heartbeat_2 = datetime.fromisoformat(
            response2.json()["seat"]["last_heartbeat"].replace("Z", "+00:00")
        )

        # Verify second heartbeat is newer
        assert last_heartbeat_2 > last_heartbeat_1

    def test_heartbeat_expired_seat_still_updates(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Heartbeat on an 'expired' seat still works (re-activates it)."""
        # Setup - create seat with old heartbeat (expired)
        create_test_table(test_db, "table-1")
        expired_time = datetime.now(UTC) - timedelta(hours=1)  # 1 hour ago
        seat = create_test_seat(
            test_db,
            seat_id="seat-1",
            table_id="table-1",
            last_heartbeat=expired_time,
        )

        # Execute
        response = client.post("/tables/table-1/seats/seat-1/heartbeat")

        # Verify - heartbeat should succeed and re-activate
        assert response.status_code == 200
        data = response.json()
        new_heartbeat = datetime.fromisoformat(
            data["seat"]["last_heartbeat"].replace("Z", "+00:00")
        )
        # New heartbeat should be recent (within last few seconds)
        assert (datetime.now(UTC) - new_heartbeat).total_seconds() < 5


# =============================================================================
# GET /tables/{table_id}/seats - List Seats Tests
# =============================================================================


class TestListSeats:
    """Tests for GET /tables/{table_id}/seats endpoint."""

    def test_list_seats_empty(self, client: TestClient, test_db: sqlite3.Connection) -> None:
        """List seats returns empty for table with no seats."""
        # Setup
        create_test_table(test_db, "table-1")

        # Execute
        response = client.get("/tables/table-1/seats")

        # Verify
        assert response.status_code == 200
        data = response.json()
        assert data["seats"] == []
        assert data["active_count"] == 0

    def test_list_seats_with_active_seats(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """List seats returns active seats."""
        # Setup
        create_test_table(test_db, "table-1")
        now = datetime.now(UTC)
        create_test_seat(test_db, seat_id="seat-1", table_id="table-1", patron_id="patron-1")
        create_test_seat(test_db, seat_id="seat-2", table_id="table-1", patron_id="patron-2")

        # Execute
        response = client.get("/tables/table-1/seats")

        # Verify
        assert response.status_code == 200
        data = response.json()
        assert len(data["seats"]) == 2
        assert data["active_count"] == 2
        seat_ids = {s["id"] for s in data["seats"]}
        assert "seat-1" in seat_ids
        assert "seat-2" in seat_ids

    def test_list_seats_filters_expired_by_default(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """List seats filters out expired seats by default (active_only=true)."""
        # Setup
        create_test_table(test_db, "table-1")
        now = datetime.now(UTC)

        # Active seat
        create_test_seat(
            test_db,
            seat_id="active-seat",
            table_id="table-1",
            last_heartbeat=now,
        )

        # Expired seat (older than TTL)
        expired_time = now - timedelta(seconds=DEFAULT_SEAT_TTL_SECONDS + 60)
        create_test_seat(
            test_db,
            seat_id="expired-seat",
            table_id="table-1",
            last_heartbeat=expired_time,
        )

        # Execute (default active_only=true)
        response = client.get("/tables/table-1/seats")

        # Verify
        assert response.status_code == 200
        data = response.json()
        assert len(data["seats"]) == 1
        assert data["seats"][0]["id"] == "active-seat"
        assert data["active_count"] == 1

    def test_list_seats_includes_expired_when_active_only_false(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """List seats includes expired seats when active_only=false."""
        # Setup
        create_test_table(test_db, "table-1")
        now = datetime.now(UTC)

        # Active seat
        create_test_seat(
            test_db,
            seat_id="active-seat",
            table_id="table-1",
            last_heartbeat=now,
        )

        # Expired seat
        expired_time = now - timedelta(seconds=DEFAULT_SEAT_TTL_SECONDS + 60)
        create_test_seat(
            test_db,
            seat_id="expired-seat",
            table_id="table-1",
            last_heartbeat=expired_time,
        )

        # Execute (active_only=false)
        response = client.get("/tables/table-1/seats?active_only=false")

        # Verify
        assert response.status_code == 200
        data = response.json()
        assert len(data["seats"]) == 2
        seat_ids = {s["id"] for s in data["seats"]}
        assert "active-seat" in seat_ids
        assert "expired-seat" in seat_ids
        # active_count still counts only active
        assert data["active_count"] == 1

    def test_list_seats_left_state_not_expired(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Seats in LEFT state are not considered expired (they explicitly left)."""
        # Setup
        create_test_table(test_db, "table-1")
        now = datetime.now(UTC)

        # Seat that left long ago
        old_time = now - timedelta(hours=1)
        create_test_seat(
            test_db,
            seat_id="left-seat",
            table_id="table-1",
            state=SeatState.LEFT,
            last_heartbeat=old_time,
        )

        # Execute (active_only=true)
        response = client.get("/tables/table-1/seats")

        # Verify - LEFT seats are active (not expired)
        data = response.json()
        # LEFT seats with old heartbeat are still included
        assert len(data["seats"]) == 1
        assert data["seats"][0]["id"] == "left-seat"
        assert data["seats"][0]["state"] == "left"

    def test_list_seats_multiple_tables(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """List seats only returns seats for the specified table."""
        # Setup
        create_test_table(test_db, "table-1")
        create_test_table(test_db, "table-2")

        create_test_seat(test_db, seat_id="seat-1", table_id="table-1")
        create_test_seat(test_db, seat_id="seat-2", table_id="table-2")

        # Execute
        response = client.get("/tables/table-1/seats")

        # Verify
        assert response.status_code == 200
        data = response.json()
        assert len(data["seats"]) == 1
        assert data["seats"][0]["id"] == "seat-1"


# =============================================================================
# Integration Flow Tests
# =============================================================================


class TestSeatsFlow:
    """End-to-end flow tests for seat operations."""

    def test_heartbeat_flow(self, client: TestClient, test_db: sqlite3.Connection) -> None:
        """Full heartbeat flow: create seat, heartbeat multiple times, verify expiry."""
        # Setup
        create_test_table(test_db, "table-1")
        old_time = datetime.now(UTC) - timedelta(minutes=4)
        create_test_seat(
            test_db,
            seat_id="seat-1",
            table_id="table-1",
            last_heartbeat=old_time,
        )

        # Initial list - seat should be present (recent enough or TTL allows)
        response = client.get("/tables/table-1/seats")
        # Note: Depending on timing, might or might not be expired

        # Heartbeat to refresh
        response = client.post("/tables/table-1/seats/seat-1/heartbeat")
        assert response.status_code == 200
        expires_at_1 = datetime.fromisoformat(response.json()["expires_at"].replace("Z", "+00:00"))

        # Wait briefly and heartbeat again
        import time

        time.sleep(0.1)
        response = client.post("/tables/table-1/seats/seat-1/heartbeat")
        assert response.status_code == 200
        expires_at_2 = datetime.fromisoformat(response.json()["expires_at"].replace("Z", "+00:00"))

        # Second expiry should be later than first
        assert expires_at_2 > expires_at_1

        # List seats - should now definitely be active
        response = client.get("/tables/table-1/seats")
        assert response.status_code == 200
        assert len(response.json()["seats"]) == 1
