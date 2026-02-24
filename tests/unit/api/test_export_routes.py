"""
Unit tests for export API routes.

Uses FastAPI TestClient with an in-memory SQLite database.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tasca.core.domain.patron import PatronId
from tasca.core.domain.saying import Speaker, SpeakerKind
from tasca.core.domain.table import Table, TableId, TableStatus, Version
from tasca.shell.api.routes.export import router
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
    """Create a FastAPI app with export router and test database."""
    app = FastAPI()

    def get_test_db() -> Generator[sqlite3.Connection, None, None]:
        yield test_db

    from tasca.shell.api.deps import get_db

    app.dependency_overrides[get_db] = get_test_db

    app.include_router(router, prefix="/tables/{table_id}/export")
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
    speaker_kind: SpeakerKind = SpeakerKind.HUMAN,
    patron_id: PatronId | None = None,
) -> None:
    """Create a test saying directly in the database."""
    speaker = Speaker(kind=speaker_kind, name=speaker_name, patron_id=patron_id)
    result = append_saying(conn, table_id, speaker, content)
    result.unwrap()


# =============================================================================
# GET /tables/{table_id}/export/jsonl - JSONL Export Tests
# =============================================================================


class TestExportJSONL:
    """Tests for GET /tables/{table_id}/export/jsonl endpoint."""

    def test_export_jsonl_not_found(self, client: TestClient) -> None:
        """Export non-existent table returns 404."""
        response = client.get("/tables/nonexistent-id/export/jsonl")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_export_jsonl_empty_table(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Export empty table returns header and table only."""
        table = create_test_table(test_db, "table-1", "Test Question?", context="Context")

        response = client.get(f"/tables/{table.id}/export/jsonl")
        assert response.status_code == 200

        lines = response.text.strip().split("\n")
        assert len(lines) == 2  # Header + table

        # Parse header
        header = json.loads(lines[0])
        assert header["type"] == "export_header"
        assert header["export_version"] == "0.1"
        assert header["table_id"] == table.id
        assert "exported_at" in header

        # Parse table
        table_line = json.loads(lines[1])
        assert table_line["type"] == "table"
        assert table_line["table"]["id"] == table.id
        assert table_line["table"]["question"] == "Test Question?"
        assert table_line["table"]["context"] == "Context"
        assert table_line["table"]["status"] == "open"

    def test_export_jsonl_with_sayings(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Export includes all sayings in sequence order."""
        table = create_test_table(test_db, "table-1", "Discussion?")
        create_test_saying(test_db, table.id, "First message", speaker_name="Alice")
        create_test_saying(test_db, table.id, "Second message", speaker_name="Bob")
        create_test_saying(test_db, table.id, "Third message", speaker_name="Charlie")

        response = client.get(f"/tables/{table.id}/export/jsonl")
        assert response.status_code == 200

        lines = response.text.strip().split("\n")
        assert len(lines) == 5  # Header + table + 3 sayings

        # Check sayings are in order (0-based sequence)
        for i, line in enumerate(lines[2:], start=0):
            saying = json.loads(line)
            assert saying["type"] == "saying"
            assert saying["saying"]["sequence"] == i
            assert saying["saying"]["table_id"] == table.id

    def test_export_jsonl_speaker_info(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Export includes correct speaker information."""
        table = create_test_table(test_db, "table-1", "Discussion?")
        create_test_saying(
            test_db,
            table.id,
            "Agent message",
            speaker_name="AgentA",
            speaker_kind=SpeakerKind.AGENT,
            patron_id=PatronId("patron-123"),
        )
        create_test_saying(
            test_db,
            table.id,
            "Human message",
            speaker_name="Alice",
            speaker_kind=SpeakerKind.HUMAN,
        )

        response = client.get(f"/tables/{table.id}/export/jsonl")
        assert response.status_code == 200

        lines = response.text.strip().split("\n")

        # Check agent saying
        agent_saying = json.loads(lines[2])
        assert agent_saying["saying"]["speaker"]["kind"] == "agent"
        assert agent_saying["saying"]["speaker"]["name"] == "AgentA"
        assert agent_saying["saying"]["speaker"]["patron_id"] == "patron-123"

        # Check human saying
        human_saying = json.loads(lines[3])
        assert human_saying["saying"]["speaker"]["kind"] == "human"
        assert human_saying["saying"]["speaker"]["name"] == "Alice"
        assert human_saying["saying"]["speaker"]["patron_id"] is None

    def test_export_jsonl_response_type(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Export returns plain text content type."""
        table = create_test_table(test_db, "table-1", "Question?")

        response = client.get(f"/tables/{table.id}/export/jsonl")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]

    def test_export_jsonl_with_download_param(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Export with download=true returns attachment header."""
        table = create_test_table(test_db, "table-1", "Question?")

        response = client.get(f"/tables/{table.id}/export/jsonl?download=true")
        assert response.status_code == 200
        assert "Content-Disposition" in response.headers
        assert f'attachment; filename="{table.id}.jsonl"' == response.headers["Content-Disposition"]

    def test_export_jsonl_without_download_param(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Export without download param has no attachment header."""
        table = create_test_table(test_db, "table-1", "Question?")

        response = client.get(f"/tables/{table.id}/export/jsonl")
        assert response.status_code == 200
        assert "Content-Disposition" not in response.headers


# =============================================================================
# GET /tables/{table_id}/export/markdown - Markdown Export Tests
# =============================================================================


class TestExportMarkdown:
    """Tests for GET /tables/{table_id}/export/markdown endpoint."""

    def test_export_markdown_not_found(self, client: TestClient) -> None:
        """Export non-existent table returns 404."""
        response = client.get("/tables/nonexistent-id/export/markdown")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_export_markdown_empty_table(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Export empty table returns markdown with metadata only."""
        table = create_test_table(
            test_db, "table-1", "What is the best approach?", context="Consider performance"
        )

        response = client.get(f"/tables/{table.id}/export/markdown")
        assert response.status_code == 200

        md = response.text
        # Check header
        assert "# What is the best approach?" in md
        assert f"table_id: {table.id}" in md
        assert "status: open" in md
        assert "version: 1" in md
        assert "context: Consider performance" in md

        # Check sections
        assert "## Board" in md
        assert "## Transcript" in md
        assert "_No sayings yet._" in md

    def test_export_markdown_with_sayings(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Export includes transcript with all sayings."""
        table = create_test_table(test_db, "table-1", "Discussion question?")
        create_test_saying(test_db, table.id, "First message here", speaker_name="Alice")
        create_test_saying(test_db, table.id, "Second message here", speaker_name="Bob")

        response = client.get(f"/tables/{table.id}/export/markdown")
        assert response.status_code == 200

        md = response.text
        # Check transcript entries (0-based sequence)
        assert "[seq=0]" in md
        assert "[seq=1]" in md
        assert "Alice" in md
        assert "Bob" in md
        assert "First message here" in md
        assert "Second message here" in md

    def test_export_markdown_speaker_format(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Export formats speakers correctly."""
        table = create_test_table(test_db, "table-1", "Question?")
        create_test_saying(
            test_db,
            table.id,
            "Agent says",
            speaker_name="AgentA",
            speaker_kind=SpeakerKind.AGENT,
            patron_id=PatronId("patron-123"),
        )
        create_test_saying(
            test_db,
            table.id,
            "Human says",
            speaker_name="Alice",
            speaker_kind=SpeakerKind.HUMAN,
        )

        response = client.get(f"/tables/{table.id}/export/markdown")
        assert response.status_code == 200

        md = response.text
        assert "(agent:AgentA)" in md
        assert "(human:Alice)" in md

    def test_export_markdown_long_content_not_truncated(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Export preserves full saying content without truncation.

        Verifies:
        - Full content is present in output
        - No truncation markers (ellipsis) are added
        - Content exceeding typical truncation limits is preserved
        """
        table = create_test_table(test_db, "table-1", "Question?")
        # Create content longer than common truncation limits (500+ chars)
        long_content = (
            "This is a very long message content that would have been truncated under old behavior. "
            * 10
        )
        create_test_saying(test_db, table.id, long_content, speaker_name="Speaker")

        response = client.get(f"/tables/{table.id}/export/markdown")
        assert response.status_code == 200

        md = response.text
        # Full content should be present (no truncation)
        assert long_content in md
        # No truncation markers (ellipsis) should appear in transcript section
        # The only "..." should be in "_No board data available." if at all
        transcript_section = md.split("## Transcript")[1]
        assert "..." not in transcript_section or long_content in transcript_section

    def test_export_markdown_response_type(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Export returns plain text content type."""
        table = create_test_table(test_db, "table-1", "Question?")

        response = client.get(f"/tables/{table.id}/export/markdown")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]

    def test_export_markdown_with_download_param(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Export with download=true returns attachment header."""
        table = create_test_table(test_db, "table-1", "Question?")

        response = client.get(f"/tables/{table.id}/export/markdown?download=true")
        assert response.status_code == 200
        assert "Content-Disposition" in response.headers
        assert f'attachment; filename="{table.id}.md"' == response.headers["Content-Disposition"]

    def test_export_markdown_without_download_param(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Export without download param has no attachment header."""
        table = create_test_table(test_db, "table-1", "Question?")

        response = client.get(f"/tables/{table.id}/export/markdown")
        assert response.status_code == 200
        assert "Content-Disposition" not in response.headers

    def test_export_markdown_table_status(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Export shows correct table status."""
        table = create_test_table(test_db, "table-1", "Question?", status=TableStatus.PAUSED)

        response = client.get(f"/tables/{table.id}/export/markdown")
        assert response.status_code == 200

        md = response.text
        assert "status: paused" in md


# =============================================================================
# Core Function Delegation Tests
# =============================================================================


class TestExportCoreDelegation:
    """Tests verifying routes delegate to core export_service functions."""

    def test_jsonl_delegates_to_generate_jsonl(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """JSONL endpoint calls core generate_jsonl function."""
        from unittest.mock import patch

        table = create_test_table(test_db, "table-1", "Test Question?")
        create_test_saying(test_db, table.id, "Test message", speaker_name="Speaker")

        with patch("tasca.shell.api.routes.export.generate_jsonl") as mock_generate:
            mock_generate.return_value = "mocked-jsonl-output"

            response = client.get(f"/tables/{table.id}/export/jsonl")

            # Verify core function was called with correct arguments
            assert mock_generate.called
            # First positional arg should be the table
            call_args = mock_generate.call_args
            assert call_args is not None
            # Check table was passed
            assert hasattr(call_args[0][0], "id")  # Table has id attribute
            # Check sayings were passed (list)
            assert isinstance(call_args[0][1], list)
            # Check exported_at was passed (ISO timestamp string)
            assert isinstance(call_args[0][2], str)
            # Verify response uses core function output
            assert response.text == "mocked-jsonl-output"

    def test_markdown_delegates_to_generate_markdown(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Markdown endpoint calls core generate_markdown function."""
        from unittest.mock import patch

        table = create_test_table(test_db, "table-1", "Test Question?")
        create_test_saying(test_db, table.id, "Test message", speaker_name="Speaker")

        with patch("tasca.shell.api.routes.export.generate_markdown") as mock_generate:
            mock_generate.return_value = "mocked-markdown-output"

            response = client.get(f"/tables/{table.id}/export/markdown")

            # Verify core function was called with correct arguments
            assert mock_generate.called
            call_args = mock_generate.call_args
            assert call_args is not None
            # Check table was passed
            assert hasattr(call_args[0][0], "id")  # Table has id attribute
            # Check sayings were passed (list)
            assert isinstance(call_args[0][1], list)
            # Verify response uses core function output
            assert response.text == "mocked-markdown-output"

    def test_jsonl_core_receives_empty_sayings_list(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Core function receives empty sayings list for table without sayings."""
        from unittest.mock import patch

        table = create_test_table(test_db, "table-1", "Empty Table?")

        with patch("tasca.shell.api.routes.export.generate_jsonl") as mock_generate:
            mock_generate.return_value = "header-and-table-only"

            client.get(f"/tables/{table.id}/export/jsonl")

            # Verify empty sayings list was passed
            call_args = mock_generate.call_args
            assert call_args is not None
            sayings = call_args[0][1]
            assert sayings == []

    def test_markdown_core_receives_ordered_sayings(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Core function receives sayings in correct order."""
        from unittest.mock import patch

        table = create_test_table(test_db, "table-1", "Order Test?")
        # Create sayings in sequence order
        create_test_saying(test_db, table.id, "First", speaker_name="A")
        create_test_saying(test_db, table.id, "Second", speaker_name="B")
        create_test_saying(test_db, table.id, "Third", speaker_name="C")

        with patch("tasca.shell.api.routes.export.generate_markdown") as mock_generate:
            mock_generate.return_value = "mocked"

            client.get(f"/tables/{table.id}/export/markdown")

            # Verify sayings are ordered by sequence
            call_args = mock_generate.call_args
            assert call_args is not None
            sayings = call_args[0][1]
            assert len(sayings) == 3
            # Verify sequence ordering
            sequences = [s.sequence for s in sayings]
            assert sequences == sorted(sequences)


# =============================================================================
# Integration Tests
# =============================================================================


class TestExportIntegration:
    """Integration tests for export functionality."""

    def test_export_after_crud_operations(
        self, client: TestClient, test_db: sqlite3.Connection
    ) -> None:
        """Export reflects correct state after CRUD operations."""
        # Create table
        table = create_test_table(
            test_db, "table-1", "Initial Question?", context="Initial context"
        )

        # Add sayings
        create_test_saying(test_db, table.id, "First message")
        create_test_saying(test_db, table.id, "Second message")
        create_test_saying(test_db, table.id, "Third message")

        # Export and verify
        response = client.get(f"/tables/{table.id}/export/jsonl")
        assert response.status_code == 200

        lines = response.text.strip().split("\n")
        assert len(lines) == 5  # Header + table + 3 sayings

        # Check markdown export too
        response = client.get(f"/tables/{table.id}/export/markdown")
        assert response.status_code == 200
        md = response.text
        assert "Initial Question?" in md
        assert "[seq=0]" in md
        assert "[seq=1]" in md
        assert "[seq=2]" in md

    def test_export_multiple_tables(self, client: TestClient, test_db: sqlite3.Connection) -> None:
        """Export different tables independently."""
        table1 = create_test_table(test_db, "table-1", "First table")
        table2 = create_test_table(test_db, "table-2", "Second table")

        create_test_saying(test_db, table1.id, "Message for table 1")
        create_test_saying(test_db, table2.id, "Message for table 2")

        # Export table 1
        response1 = client.get(f"/tables/{table1.id}/export/jsonl")
        lines1 = response1.text.strip().split("\n")
        assert len(lines1) == 3  # Header + table + 1 saying

        # Export table 2
        response2 = client.get(f"/tables/{table2.id}/export/jsonl")
        lines2 = response2.text.strip().split("\n")
        assert len(lines2) == 3  # Header + table + 1 saying

        # Verify different content
        assert table1.id in response1.text
        assert table2.id not in response1.text
        assert table2.id in response2.text
        assert table1.id not in response2.text
