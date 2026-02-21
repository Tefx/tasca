"""
Unit tests for patrons API routes.

Uses FastAPI TestClient with an in-memory SQLite database.
"""

from __future__ import annotations

import sqlite3
from typing import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tasca.shell.api.routes.patrons import router
from tasca.shell.storage.database import apply_schema


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def test_db() -> Generator[sqlite3.Connection, None, None]:
    """Create an in-memory database with patrons schema."""
    # check_same_thread=False is needed for FastAPI TestClient which uses threads
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    apply_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def app(test_db: sqlite3.Connection) -> FastAPI:
    """Create a FastAPI app with patrons router and test database."""
    app = FastAPI()

    # Override the get_db dependency to use test database
    def get_test_db() -> Generator[sqlite3.Connection, None, None]:
        yield test_db

    from tasca.shell.api.deps import get_db

    app.dependency_overrides[get_db] = get_test_db

    app.include_router(router, prefix="/patrons")
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)


# =============================================================================
# POST /patrons - Register Tests
# =============================================================================


class TestRegisterPatron:
    """Tests for POST /patrons endpoint."""

    def test_register_patron_success(self, client: TestClient) -> None:
        """Register a new patron successfully."""
        response = client.post(
            "/patrons",
            json={"display_name": "Test Agent", "kind": "agent"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["name"] == "Test Agent"
        assert data["kind"] == "agent"
        assert data["is_new"] is True
        assert "created_at" in data

    def test_register_patron_default_kind(self, client: TestClient) -> None:
        """Register patron with default kind."""
        response = client.post(
            "/patrons",
            json={"display_name": "Default Agent"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["kind"] == "agent"
        assert data["is_new"] is True

    def test_register_patron_human_kind(self, client: TestClient) -> None:
        """Register patron with human kind."""
        response = client.post(
            "/patrons",
            json={"display_name": "Human User", "kind": "human"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Human User"
        assert data["kind"] == "human"
        assert data["is_new"] is True

    def test_register_patron_dedup_returns_existing(self, client: TestClient) -> None:
        """Registering same display_name returns existing patron with is_new=False."""
        # First registration
        response1 = client.post(
            "/patrons",
            json={"display_name": "Duplicate Agent"},
        )
        assert response1.status_code == 200
        data1 = response1.json()
        assert data1["is_new"] is True
        first_id = data1["id"]

        # Second registration with same display_name - should return existing
        response2 = client.post(
            "/patrons",
            json={"display_name": "Duplicate Agent"},
        )
        assert response2.status_code == 200
        data2 = response2.json()
        assert data2["is_new"] is False
        assert data2["id"] == first_id  # Same ID
        assert data2["name"] == "Duplicate Agent"

    def test_register_patron_different_names_different_ids(self, client: TestClient) -> None:
        """Registering different display_names creates different patrons."""
        response1 = client.post(
            "/patrons",
            json={"display_name": "Agent One"},
        )
        response2 = client.post(
            "/patrons",
            json={"display_name": "Agent Two"},
        )

        assert response1.status_code == 200
        assert response2.status_code == 200

        data1 = response1.json()
        data2 = response2.json()

        assert data1["id"] != data2["id"]
        assert data1["is_new"] is True
        assert data2["is_new"] is True

    def test_register_patron_missing_display_name(self, client: TestClient) -> None:
        """Register with missing display_name returns 422."""
        response = client.post(
            "/patrons",
            json={"kind": "agent"},
        )
        assert response.status_code == 422  # Validation error


# =============================================================================
# GET /patrons/{patron_id} - Get Tests
# =============================================================================


class TestGetPatron:
    """Tests for GET /patrons/{patron_id} endpoint."""

    def test_get_patron_not_found(self, client: TestClient) -> None:
        """Get non-existent patron returns 404."""
        response = client.get("/patrons/nonexistent-id")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_patron_exists(self, client: TestClient) -> None:
        """Get existing patron returns the patron."""
        # Create a patron
        create_response = client.post(
            "/patrons",
            json={"display_name": "Existing Agent", "kind": "agent"},
        )
        patron_id = create_response.json()["id"]

        # Get the patron
        response = client.get(f"/patrons/{patron_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == patron_id
        assert data["name"] == "Existing Agent"
        assert data["kind"] == "agent"


# =============================================================================
# Integration Flow Tests
# =============================================================================


class TestPatronFlow:
    """End-to-end flow tests for patron operations."""

    def test_register_get_flow(self, client: TestClient) -> None:
        """Full register and get flow for a patron."""
        # Register
        register_response = client.post(
            "/patrons",
            json={"display_name": "Flow Agent", "kind": "agent"},
        )
        assert register_response.status_code == 200
        data = register_response.json()
        patron_id = data["id"]
        assert data["is_new"] is True

        # Get
        get_response = client.get(f"/patrons/{patron_id}")
        assert get_response.status_code == 200
        assert get_response.json()["name"] == "Flow Agent"

        # Register duplicate
        dup_response = client.post(
            "/patrons",
            json={"display_name": "Flow Agent"},
        )
        assert dup_response.status_code == 200
        assert dup_response.json()["is_new"] is False
        assert dup_response.json()["id"] == patron_id

    def test_multiple_patrons(self, client: TestClient) -> None:
        """Multiple patrons can be registered and retrieved."""
        # Register multiple patrons
        ids = []
        for i in range(3):
            response = client.post(
                "/patrons",
                json={"display_name": f"Agent-{i}", "kind": "agent"},
            )
            assert response.status_code == 200
            ids.append(response.json()["id"])

        # Verify all have unique IDs
        assert len(set(ids)) == 3

        # Verify each can be retrieved
        for i, patron_id in enumerate(ids):
            response = client.get(f"/patrons/{patron_id}")
            assert response.status_code == 200
            assert response.json()["name"] == f"Agent-{i}"
