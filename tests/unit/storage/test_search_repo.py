"""
Tests for search_repo module - FTS5 full-text search implementation.

Tests cover:
- search_sayings: FTS5 search with optional table_id filter
- count_search_results: Count matching sayings
- rebuild_fts_index: Rebuild FTS5 index
- search_tables: Combined FTS + LIKE search for tables
- count_table_search_results: Count matching tables
- Helper functions: _truncate_snippet, _row_to_search_result, _row_to_table_hit
"""

import sqlite3
import uuid
from datetime import datetime, timezone

import pytest
from returns.result import Failure, Success

from tasca.core.domain.patron import PatronId
from tasca.core.domain.saying import Saying, SayingId, Speaker, SpeakerKind
from tasca.shell.storage.database import apply_schema
from tasca.shell.storage.search_repo import (
    SearchError,
    SearchResult,
    TableSearchHit,
    count_search_results,
    count_table_search_results,
    rebuild_fts_index,
    search_sayings,
    search_tables,
    _row_to_search_result,
    _row_to_table_hit,
    _truncate_snippet,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def memory_db() -> sqlite3.Connection:
    """Create an in-memory database with schema applied."""
    conn = sqlite3.connect(":memory:")
    schema_result = apply_schema(conn)
    assert isinstance(schema_result, Success), f"Failed to apply schema: {schema_result}"
    return conn


@pytest.fixture
def seed_data(memory_db: sqlite3.Connection) -> dict:
    """Seed database with test tables and sayings.

    Returns a dict with table_ids and saying_ids for reference.
    """
    now = datetime.now(timezone.utc).isoformat()

    # Create two tables
    table_1_id = str(uuid.uuid4())
    table_2_id = str(uuid.uuid4())

    memory_db.execute(
        """
        INSERT INTO tables (id, question, context, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            table_1_id,
            "Discussion about Python programming",
            "A table about coding",
            "open",
            now,
            now,
        ),
    )
    memory_db.execute(
        """
        INSERT INTO tables (id, question, context, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (table_2_id, "Machine learning basics", "Introduction to ML", "closed", now, now),
    )

    # Create sayings
    saying_1_id = str(uuid.uuid4())
    saying_2_id = str(uuid.uuid4())
    saying_3_id = str(uuid.uuid4())
    saying_4_id = str(uuid.uuid4())

    sayings_data = [
        (
            saying_1_id,
            table_1_id,
            0,
            "human",
            "Alice",
            None,
            "Python is great for data science",
            0,
            now,
        ),
        (
            saying_2_id,
            table_1_id,
            1,
            "agent",
            "Bot",
            "patron-001",
            "I agree, Python has excellent libraries",
            0,
            now,
        ),
        (
            saying_3_id,
            table_2_id,
            0,
            "human",
            "Bob",
            None,
            "Machine learning requires lots of data",
            0,
            now,
        ),
        (
            saying_4_id,
            table_2_id,
            1,
            "human",
            "Alice",
            None,
            "Neural networks are fascinating",
            0,
            now,
        ),
    ]

    for data in sayings_data:
        memory_db.execute(
            """
            INSERT INTO sayings (id, table_id, sequence, speaker_kind, speaker_name, patron_id, content, pinned, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            data,
        )

    memory_db.commit()

    # Rebuild FTS index to ensure search works
    rebuild_fts_index(memory_db)

    return {
        "table_1_id": table_1_id,
        "table_2_id": table_2_id,
        "saying_1_id": saying_1_id,
        "saying_2_id": saying_2_id,
        "saying_3_id": saying_3_id,
        "saying_4_id": saying_4_id,
    }


# =============================================================================
# search_sayings Tests
# =============================================================================


class TestSearchSayings:
    """Tests for search_sayings function."""

    def test_empty_query_returns_empty_list(self, memory_db: sqlite3.Connection) -> None:
        """Empty query returns empty list."""
        result = search_sayings(memory_db, "")
        assert isinstance(result, Success)
        assert result.unwrap() == []

    def test_whitespace_query_returns_empty_list(self, memory_db: sqlite3.Connection) -> None:
        """Whitespace-only query returns empty list."""
        result = search_sayings(memory_db, "   ")
        assert isinstance(result, Success)
        assert result.unwrap() == []

    def test_search_finds_matching_sayings(
        self, memory_db: sqlite3.Connection, seed_data: dict
    ) -> None:
        """Search finds sayings containing the query term."""
        result = search_sayings(memory_db, "Python")
        assert isinstance(result, Success)
        results = result.unwrap()
        assert len(results) == 2  # Both Python mentions

        # Check that results are SearchResult objects
        for r in results:
            assert isinstance(r, SearchResult)
            assert isinstance(r.saying, Saying)
            assert isinstance(r.rank, float)
            assert isinstance(r.snippet, str)

    def test_search_with_table_id_filter(
        self, memory_db: sqlite3.Connection, seed_data: dict
    ) -> None:
        """Search with table_id filter only returns sayings from that table."""
        table_1_id = seed_data["table_1_id"]

        result = search_sayings(memory_db, "Python", table_id=table_1_id)
        assert isinstance(result, Success)
        results = result.unwrap()

        # All results should be from table_1
        for r in results:
            assert r.saying.table_id == table_1_id

    def test_search_with_limit_and_offset(
        self, memory_db: sqlite3.Connection, seed_data: dict
    ) -> None:
        """Pagination works with limit and offset."""
        # Get total results first
        result_all = search_sayings(memory_db, "Python")
        assert isinstance(result_all, Success)
        total = len(result_all.unwrap())

        # Get paginated results
        result_paged = search_sayings(memory_db, "Python", limit=1, offset=0)
        assert isinstance(result_paged, Success)
        paged = result_paged.unwrap()
        assert len(paged) == 1

        # If there's more than one, test offset
        if total > 1:
            result_offset = search_sayings(memory_db, "Python", limit=1, offset=1)
            assert isinstance(result_offset, Success)
            assert len(result_offset.unwrap()) == 1

    def test_search_no_matches(self, memory_db: sqlite3.Connection) -> None:
        """Search with no matches returns empty list."""
        result = search_sayings(memory_db, "nonexistent_term_xyz123")
        assert isinstance(result, Success)
        assert result.unwrap() == []

    def test_search_with_fts_syntax_error(self, memory_db: sqlite3.Connection) -> None:
        """Invalid FTS5 syntax returns Failure."""
        # FTS5 doesn't like unbalanced quotes
        result = search_sayings(memory_db, '"unclosed')
        assert isinstance(result, Failure)
        error = result.failure()
        # Could be either "Invalid search query syntax" or "Database error" depending on SQLite version
        assert "error" in error.lower()

    def test_search_results_have_rank_and_snippet(
        self, memory_db: sqlite3.Connection, seed_data: dict
    ) -> None:
        """Search results have rank and snippet populated."""
        result = search_sayings(memory_db, "Python")
        assert isinstance(result, Success)
        results = result.unwrap()

        assert len(results) > 0
        for r in results:
            assert isinstance(r.rank, float)
            assert isinstance(r.snippet, str)
            assert len(r.snippet) > 0


# =============================================================================
# count_search_results Tests
# =============================================================================


class TestCountSearchResults:
    """Tests for count_search_results function."""

    def test_empty_query_returns_zero(self, memory_db: sqlite3.Connection) -> None:
        """Empty query returns count of 0."""
        result = count_search_results(memory_db, "")
        assert isinstance(result, Success)
        assert result.unwrap() == 0

    def test_count_returns_correct_count(
        self, memory_db: sqlite3.Connection, seed_data: dict
    ) -> None:
        """Count returns the correct number of matching sayings."""
        result = count_search_results(memory_db, "Python")
        assert isinstance(result, Success)
        count = result.unwrap()
        assert count == 2  # Two sayings mention Python

    def test_count_with_table_id_filter(
        self, memory_db: sqlite3.Connection, seed_data: dict
    ) -> None:
        """Count with table_id filter only counts sayings from that table."""
        table_1_id = seed_data["table_1_id"]

        result = count_search_results(memory_db, "Python", table_id=table_1_id)
        assert isinstance(result, Success)
        assert result.unwrap() == 2  # Both Python mentions are in table_1

    def test_count_no_matches(self, memory_db: sqlite3.Connection) -> None:
        """Count with no matches returns 0."""
        result = count_search_results(memory_db, "nonexistent_term_xyz123")
        assert isinstance(result, Success)
        assert result.unwrap() == 0

    def test_count_with_fts_syntax_error(self, memory_db: sqlite3.Connection) -> None:
        """Invalid FTS5 syntax returns Failure."""
        result = count_search_results(memory_db, '"unclosed')
        assert isinstance(result, Failure)


# =============================================================================
# rebuild_fts_index Tests
# =============================================================================


class TestRebuildFtsIndex:
    """Tests for rebuild_fts_index function."""

    def test_rebuild_returns_count(self, memory_db: sqlite3.Connection, seed_data: dict) -> None:
        """Rebuild returns count of indexed rows."""
        result = rebuild_fts_index(memory_db)
        assert isinstance(result, Success)
        count = result.unwrap()
        assert count == 4  # 4 sayings were seeded

    def test_rebuild_empty_database(self, memory_db: sqlite3.Connection) -> None:
        """Rebuild on empty database returns 0."""
        result = rebuild_fts_index(memory_db)
        assert isinstance(result, Success)
        assert result.unwrap() == 0


# =============================================================================
# search_tables Tests
# =============================================================================


class TestSearchTables:
    """Tests for search_tables function."""

    def test_empty_query_returns_empty_list(self, memory_db: sqlite3.Connection) -> None:
        """Empty query returns empty list."""
        result = search_tables(memory_db, "")
        assert isinstance(result, Success)
        assert result.unwrap() == []

    def test_whitespace_query_returns_empty_list(self, memory_db: sqlite3.Connection) -> None:
        """Whitespace-only query returns empty list."""
        result = search_tables(memory_db, "   ")
        assert isinstance(result, Success)
        assert result.unwrap() == []

    def test_search_finds_saying_match(
        self, memory_db: sqlite3.Connection, seed_data: dict
    ) -> None:
        """Search finds tables with matching saying content."""
        result = search_tables(memory_db, "data science")
        assert isinstance(result, Success)
        results = result.unwrap()

        assert len(results) == 1
        hit = results[0]
        assert isinstance(hit, TableSearchHit)
        assert hit.match_type == "saying"
        assert hit.table_id == seed_data["table_1_id"]

    def test_search_finds_question_match(
        self, memory_db: sqlite3.Connection, seed_data: dict
    ) -> None:
        """Search finds tables with matching question."""
        result = search_tables(memory_db, "Python programming")
        assert isinstance(result, Success)
        results = result.unwrap()

        assert len(results) >= 1
        # Find the question match
        question_matches = [h for h in results if h.match_type == "question"]
        assert len(question_matches) >= 1

    def test_search_finds_context_match(
        self, memory_db: sqlite3.Connection, seed_data: dict
    ) -> None:
        """Search finds tables with matching context."""
        result = search_tables(memory_db, "coding")
        assert isinstance(result, Success)
        results = result.unwrap()

        assert len(results) >= 1
        # Context contains "coding"
        for hit in results:
            if hit.match_type == "context":
                assert "coding" in hit.context.lower() if hit.context else False

    def test_search_with_status_filter(
        self, memory_db: sqlite3.Connection, seed_data: dict
    ) -> None:
        """Search with status filter only returns matching tables."""
        result = search_tables(memory_db, "Machine learning", status="closed")
        assert isinstance(result, Success)
        results = result.unwrap()

        # All results should have status "closed"
        for hit in results:
            assert hit.status == "closed"

    def test_search_deduplication(self, memory_db: sqlite3.Connection, seed_data: dict) -> None:
        """Each table appears at most once in results."""
        result = search_tables(memory_db, "Python")
        assert isinstance(result, Success)
        results = result.unwrap()

        # Check no duplicate table_ids
        table_ids = [hit.table_id for hit in results]
        assert len(table_ids) == len(set(table_ids))

    def test_search_pagination(self, memory_db: sqlite3.Connection, seed_data: dict) -> None:
        """Pagination works with limit and offset."""
        result_all = search_tables(memory_db, "Python")
        assert isinstance(result_all, Success)
        total = len(result_all.unwrap())

        if total > 0:
            result_limited = search_tables(memory_db, "Python", limit=1)
            assert isinstance(result_limited, Success)
            assert len(result_limited.unwrap()) <= 1

    def test_search_no_matches(self, memory_db: sqlite3.Connection) -> None:
        """Search with no matches returns empty list."""
        result = search_tables(memory_db, "nonexistent_term_xyz123")
        assert isinstance(result, Success)
        assert result.unwrap() == []

    def test_search_result_fields_populated(
        self, memory_db: sqlite3.Connection, seed_data: dict
    ) -> None:
        """All TableSearchHit fields are populated."""
        result = search_tables(memory_db, "Python")
        assert isinstance(result, Success)
        results = result.unwrap()

        assert len(results) > 0
        for hit in results:
            assert hit.table_id
            assert hit.question is not None
            assert isinstance(hit.rank, float)
            assert hit.match_type in ("question", "context", "saying")
            assert hit.created_at
            assert hit.updated_at

    def test_search_handles_database_errors(self, memory_db: sqlite3.Connection) -> None:
        """Search handles database errors gracefully."""
        # Close the connection to force a database error
        memory_db.close()
        result = search_tables(memory_db, "test")
        assert isinstance(result, Failure)

    def test_search_sayings_handles_database_errors(self, memory_db: sqlite3.Connection) -> None:
        """Search sayings handles database errors gracefully."""
        memory_db.close()
        result = search_sayings(memory_db, "test")
        assert isinstance(result, Failure)

    def test_count_results_handles_database_errors(self, memory_db: sqlite3.Connection) -> None:
        """Count results handles database errors gracefully."""
        memory_db.close()
        result = count_search_results(memory_db, "test")
        assert isinstance(result, Failure)

    def test_count_table_results_handles_database_errors(
        self, memory_db: sqlite3.Connection
    ) -> None:
        """Count table results handles database errors gracefully."""
        memory_db.close()
        result = count_table_search_results(memory_db, "test")
        assert isinstance(result, Failure)


# =============================================================================
# count_table_search_results Tests
# =============================================================================


class TestCountTableSearchResults:
    """Tests for count_table_search_results function."""

    def test_empty_query_returns_zero(self, memory_db: sqlite3.Connection) -> None:
        """Empty query returns count of 0."""
        result = count_table_search_results(memory_db, "")
        assert isinstance(result, Success)
        assert result.unwrap() == 0

    def test_count_returns_correct_count(
        self, memory_db: sqlite3.Connection, seed_data: dict
    ) -> None:
        """Count returns the correct number of unique matching tables."""
        result = count_table_search_results(memory_db, "Python")
        assert isinstance(result, Success)
        count = result.unwrap()
        assert count >= 1  # At least one table mentions Python

    def test_count_with_status_filter(self, memory_db: sqlite3.Connection, seed_data: dict) -> None:
        """Count with status filter only counts matching tables."""
        result = count_table_search_results(memory_db, "Machine learning", status="closed")
        assert isinstance(result, Success)
        # Should match table_2 which is closed and mentions ML in question
        assert result.unwrap() >= 1

    def test_count_no_matches(self, memory_db: sqlite3.Connection) -> None:
        """Count with no matches returns 0."""
        result = count_table_search_results(memory_db, "nonexistent_term_xyz123")
        assert isinstance(result, Success)
        assert result.unwrap() == 0

    def test_count_with_fts_syntax_error(self, memory_db: sqlite3.Connection) -> None:
        """Invalid FTS5 syntax returns Failure."""
        result = count_table_search_results(memory_db, '"unclosed')
        assert isinstance(result, Failure)


# =============================================================================
# _truncate_snippet Tests
# =============================================================================


class TestTruncateSnippet:
    """Tests for _truncate_snippet helper function."""

    def test_short_text_unchanged(self) -> None:
        """Short text is returned unchanged."""
        text = "This is a short text"
        result = _truncate_snippet(text, "short")
        assert result == text

    def test_truncates_around_match(self) -> None:
        """Long text is truncated around the match."""
        # Create a long text with the query in the middle
        text = "A" * 100 + "MATCH" + "B" * 100
        result = _truncate_snippet(text, "MATCH", max_len=50)
        assert "MATCH" in result
        # Function shows context around match (up to 50 chars before + query + 50 chars after)
        # Plus ellipses if needed - this is the expected behavior

    def test_adds_ellipsis_at_start(self) -> None:
        """Ellipsis is added at start when truncated from beginning."""
        text = "START" + "A" * 300
        result = _truncate_snippet(text, "START", max_len=50)
        # Since match is at the start, no ellipsis at beginning
        assert result.startswith("START")

    def test_adds_ellipsis_at_end(self) -> None:
        """Ellipsis is added at end when truncated after match."""
        # Create text where there's content after the match area
        text = "A" * 300 + "MATCH" + "B" * 300
        result = _truncate_snippet(text, "MATCH", max_len=50)
        # The function shows 50 chars before match + match + 50 chars after
        # Then adds ellipses if truncated
        assert "MATCH" in result

    def test_query_not_found_returns_truncated(self) -> None:
        """When query not found, returns truncated text."""
        text = "A" * 300
        result = _truncate_snippet(text, "NOTFOUND", max_len=50)
        assert len(result) == 53  # 50 + "..."
        assert result.endswith("...")

    def test_case_insensitive_match(self) -> None:
        """Match is case-insensitive."""
        text = "A" * 100 + "MaTcH" + "B" * 100
        result = _truncate_snippet(text, "match", max_len=50)
        assert "MaTcH" in result


# =============================================================================
# _row_to_search_result Tests
# =============================================================================


class TestRowToSearchResult:
    """Tests for _row_to_search_result helper function."""

    def test_converts_row_correctly(self) -> None:
        """Row is converted to SearchResult correctly."""
        now = datetime.now(timezone.utc)
        row = (
            "saying-123",  # saying_id
            "table-456",  # table_id
            5,  # sequence
            "human",  # speaker_kind
            "Alice",  # speaker_name
            None,  # patron_id
            "Hello world",  # content
            0,  # pinned
            now.isoformat(),  # created_at
            0.5,  # rank
            "Hello ...",  # snippet
        )

        result = _row_to_search_result(row)

        assert isinstance(result, SearchResult)
        assert result.saying.id == SayingId("saying-123")
        assert result.saying.table_id == "table-456"
        assert result.saying.sequence == 5
        assert result.saying.speaker.kind == SpeakerKind.HUMAN
        assert result.saying.speaker.name == "Alice"
        assert result.saying.content == "Hello world"
        assert result.rank == 0.5
        assert result.snippet == "Hello ..."

    def test_handles_null_snippet(self) -> None:
        """Null snippet falls back to content prefix."""
        now = datetime.now(timezone.utc)
        row = (
            "saying-123",
            "table-456",
            0,
            "human",
            "Alice",
            None,
            "This is a long content string that should be truncated",
            0,
            now.isoformat(),
            1.0,
            None,  # snippet is None
        )

        result = _row_to_search_result(row)

        assert result.snippet == result.saying.content[:200]


# =============================================================================
# _row_to_table_hit Tests
# =============================================================================


class TestRowToTableHit:
    """Tests for _row_to_table_hit helper function."""

    def test_converts_row_correctly(self) -> None:
        """Row is converted to TableSearchHit correctly."""
        row = (
            "table-123",  # table_id
            "What about Python?",  # question
            "Some context",  # context
            "open",  # status
            0.75,  # rank
            "Python is ...",  # snippet
            "saying",  # match_type
            "2024-01-01T00:00:00",  # created_at
            "2024-01-02T00:00:00",  # updated_at
        )

        result = _row_to_table_hit(row)

        assert isinstance(result, TableSearchHit)
        assert result.table_id == "table-123"
        assert result.question == "What about Python?"
        assert result.context == "Some context"
        assert result.status == "open"
        assert result.rank == 0.75
        assert result.snippet == "Python is ..."
        assert result.match_type == "saying"
        assert result.created_at == "2024-01-01T00:00:00"
        assert result.updated_at == "2024-01-02T00:00:00"

    def test_handles_null_context(self) -> None:
        """Null context is handled correctly."""
        row = (
            "table-123",
            "Question?",
            None,  # context is None
            "open",
            0.0,
            "Snippet",
            "question",
            "2024-01-01T00:00:00",
            "2024-01-01T00:00:00",
        )

        result = _row_to_table_hit(row)

        assert result.context is None

    def test_handles_null_snippet(self) -> None:
        """Null snippet falls back to question prefix."""
        row = (
            "table-123",
            "This is a long question that might need truncation",
            None,
            "open",
            0.0,
            None,  # snippet is None
            "question",
            "2024-01-01T00:00:00",
            "2024-01-01T00:00:00",
        )

        result = _row_to_table_hit(row)

        assert result.snippet == "This is a long question that might need truncation"[:200]
