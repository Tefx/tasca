"""
Unit tests for search API routes.

Uses FastAPI TestClient with an in-memory SQLite database.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tasca.core.domain.patron import PatronId
from tasca.core.domain.saying import Saying, SayingId, Speaker, SpeakerKind
from tasca.core.domain.table import Table, TableId, TableStatus, Version
from tasca.shell.api.routes.search import router
from tasca.shell.storage.database import apply_schema
from tasca.shell.storage.saying_repo import append_saying
from tasca.shell.storage.table_repo import create_table


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def test_db() -> Generator[sqlite3.Connection, None, None]:
    """Create an in-memory database with tables schema."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    apply_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def app(test_db: sqlite3.Connection) -> FastAPI:
    """Create a FastAPI app with search router and test database."""
    app = FastAPI()

    def get_test_db() -> Generator[sqlite3.Connection, None, None]:
        yield test_db

    from tasca.shell.api.deps import get_db

    app.dependency_overrides[get_db] = get_test_db

    app.include_router(router, prefix="/search")
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create a test client for the FastAPI app."""
    return TestClient(app)


# =============================================================================
# Helper Functions
# =============================================================================


def create_test_table(
    conn: sqlite3.Connection,
    table_id: str,
    question: str,
    context: str | None = None,
    status: TableStatus = TableStatus.OPEN,
) -> Table:
    """Create a test table directly in the database."""
    now = datetime.now(timezone.utc)
    table = Table(
        id=TableId(table_id),
        question=question,
        context=context,
        status=status,
        version=Version(1),
        created_at=now,
        updated_at=now,
    )
    result = create_table(conn, table)
    return result.unwrap()


def create_test_saying(
    conn: sqlite3.Connection,
    table_id: str,
    content: str,
    speaker_name: str = "Test Speaker",
) -> Saying:
    """Create a test saying directly in the database."""
    speaker = Speaker(kind=SpeakerKind.HUMAN, name=speaker_name, patron_id=None)
    result = append_saying(conn, table_id, speaker, content)
    return result.unwrap()


# =============================================================================
# GET /search - Search Tests
# =============================================================================


class TestSearchEndpoint:
    """Tests for GET /search endpoint."""

    def test_search_empty_database(self, client: TestClient) -> None:
        """Search returns empty results when database is empty."""
        response = client.get("/search?q=test")
        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "test"
        assert data["total"] == 0
        assert data["hits"] == []

    def test_search_requires_query(self, client: TestClient) -> None:
        """Search requires 'q' query parameter."""
        response = client.get("/search")
        assert response.status_code == 422  # Validation error

    def test_search_empty_query(self, client: TestClient) -> None:
        """Search with empty query returns validation error."""
        response = client.get("/search?q=")
        assert response.status_code == 422  # Validation error (min_length=1)

    def test_search_finds_table_question(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Search finds matches in table questions."""
        create_test_table(test_db, "table-1", "What is the best approach?")
        create_test_table(test_db, "table-2", "How to handle errors?")

        response = client.get("/search?q=approach")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["hits"]) == 1
        assert data["hits"][0]["table_id"] == "table-1"
        assert data["hits"][0]["match_type"] == "question"
        assert "approach" in data["hits"][0]["snippet"].lower()

    def test_search_finds_table_context(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Search finds matches in table context."""
        create_test_table(
            test_db, "table-1", "Question?", context="Consider performance optimization"
        )
        create_test_table(test_db, "table-2", "Another?", context="Different discussion")

        response = client.get("/search?q=optimization")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["hits"][0]["match_type"] == "context"
        assert "optimization" in data["hits"][0]["snippet"].lower()

    def test_search_finds_saying_content(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Search finds matches in saying content using FTS5."""
        create_test_table(test_db, "table-1", "Discussion question?")
        create_test_saying(test_db, "table-1", "This is about machine learning algorithms")
        create_test_saying(test_db, "table-1", "Another point about data processing")

        # FTS5 tokenizes 'machine learning' as two tokens
        # Use quoted phrase for exact match
        response = client.get('/search?q="machine+learning"')
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["hits"][0]["match_type"] == "saying"
        # Snippet shows the matched phrase
        assert "machine" in data["hits"][0]["snippet"].lower()

    def test_search_filter_by_status(self, client: TestClient, test_db: sqlite3.Connection) -> None:
        """Search can filter by table status."""
        create_test_table(test_db, "table-1", "Open question", status=TableStatus.OPEN)
        create_test_table(test_db, "table-2", "Paused question", status=TableStatus.PAUSED)

        response = client.get("/search?q=question&status=open")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["hits"][0]["table_id"] == "table-1"

    def test_search_invalid_status(self, client: TestClient) -> None:
        """Search with invalid status returns 400."""
        response = client.get("/search?q=test&status=invalid")
        assert response.status_code == 400
        assert "invalid status" in response.json()["detail"].lower()

    def test_search_pagination(self, client: TestClient, test_db: sqlite3.Connection) -> None:
        """Search supports pagination."""
        # Create multiple tables
        for i in range(10):
            create_test_table(test_db, f"table-{i}", f"Test question {i}")

        # First page
        response = client.get("/search?q=test&limit=5&offset=0")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 10
        assert len(data["hits"]) == 5

        # Second page
        response = client.get("/search?q=test&limit=5&offset=5")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 10
        assert len(data["hits"]) == 5

    def test_search_case_insensitive(self, client: TestClient, test_db: sqlite3.Connection) -> None:
        """Search is case insensitive."""
        create_test_table(test_db, "table-1", "Important Discussion About Architecture")

        response = client.get("/search?q=ARCHITECTURE")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

        response = client.get("/search?q=architecture")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

    def test_search_no_duplicate_tables(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Search returns each table only once even if multiple matches."""
        create_test_table(test_db, "table-1", "Test question")
        create_test_saying(test_db, "table-1", "Test saying one")
        create_test_saying(test_db, "table-1", "Test saying two")

        response = client.get("/search?q=test")
        assert response.status_code == 200
        data = response.json()
        # Should only have one hit for the table (matches question first)
        assert data["total"] == 1

    def test_search_snippet_truncation(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Search truncates long snippets."""
        # Use realistic text instead of repeated chars (FTS5 tokenizer behavior)
        long_content = (
            "Lorem ipsum dolor sit amet consectetur adipiscing elit " * 10
            + "FOUND_KEYWORD "
            + "sed do eiusmod tempor incididunt ut labore et dolore magna aliqua " * 10
        )
        create_test_table(test_db, "table-1", "Question?")
        create_test_saying(test_db, "table-1", long_content)

        response = client.get("/search?q=FOUND_KEYWORD")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        snippet = data["hits"][0]["snippet"]
        # Snippet should contain the keyword
        assert "FOUND_KEYWORD" in snippet
        # Snippet should be truncated with ellipsis
        assert len(snippet) < len(long_content)


# =============================================================================
# Edge Cases
# =============================================================================


class TestSearchEdgeCases:
    """Tests for edge cases in search."""

    def test_search_special_characters(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Search handles special characters."""
        create_test_table(test_db, "table-1", "Question about C++ and Python?")

        # The query should work even with special characters
        response = client.get("/search?q=python")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1

    def test_search_multiple_tables_same_match(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Search returns multiple tables when they match."""
        create_test_table(test_db, "table-1", "Discussion about testing")
        create_test_table(test_db, "table-2", "Another testing topic")
        create_test_table(test_db, "table-3", "Unrelated topic")

        response = client.get("/search?q=testing")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        table_ids = {hit["table_id"] for hit in data["hits"]}
        assert "table-1" in table_ids
        assert "table-2" in table_ids
        assert "table-3" not in table_ids
