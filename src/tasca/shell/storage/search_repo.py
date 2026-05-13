"""Search repository - FTS5 full-text search implementation."""

import sqlite3
from dataclasses import dataclass
from typing import Any, NewType, cast

from returns.result import Failure, Result, Success

from tasca.core.domain.saying import Saying
from tasca.core.storage_rows import row_to_saying
from tasca.core.storage_rows import truncate_snippet as _truncate_snippet

# Type for repository errors
SearchError = NewType("SearchError", str)


@dataclass(frozen=True)
class SearchResult:
    """A saying search result with relevance score and snippet."""

    saying: Saying
    rank: float
    snippet: str


# @shell_complexity: 4 branches for FTS query with optional table_id filter + error handling
def search_sayings(
    conn: sqlite3.Connection,
    query: str,
    table_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Result[list[SearchResult], SearchError]:
    """Search sayings using FTS5 full-text search."""
    if not query or not query.strip():
        return Success([])

    try:
        if table_id:
            sql = """
                SELECT
                    s.id, s.table_id, s.sequence, s.speaker_kind, s.speaker_name,
                    s.patron_id, s.content, s.pinned, s.created_at,
                    fts.rank,
                    snippet(sayings_fts, -1, '...', '...', '...', 32) as snippet
                FROM sayings_fts fts
                JOIN sayings s ON fts.rowid = s.rowid
                WHERE sayings_fts MATCH ? AND s.table_id = ?
                ORDER BY fts.rank
                LIMIT ? OFFSET ?
            """
            params: tuple[object, ...] = (query, table_id, limit, offset)
        else:
            sql = """
                SELECT
                    s.id, s.table_id, s.sequence, s.speaker_kind, s.speaker_name,
                    s.patron_id, s.content, s.pinned, s.created_at,
                    fts.rank,
                    snippet(sayings_fts, -1, '...', '...', '...', 32) as snippet
                FROM sayings_fts fts
                JOIN sayings s ON fts.rowid = s.rowid
                WHERE sayings_fts MATCH ?
                ORDER BY fts.rank
                LIMIT ? OFFSET ?
            """
            params = (query, limit, offset)

        rows = conn.execute(sql, params).fetchall()
        results: list[SearchResult] = []
        for row in rows:
            try:
                results.append(cast(SearchResult, _row_to_search_result(row)))
            except (IndexError, TypeError, ValueError) as exc:
                return Failure(SearchError(f"Malformed search result row: {exc}"))
        return Success(results)

    except sqlite3.Error as e:
        error_msg = str(e).lower()
        if "fts5" in error_msg or "match" in error_msg or "syntax" in error_msg:
            return Failure(SearchError(f"Invalid search query syntax: {e}"))
        return Failure(SearchError(f"Database error: {e}"))


# @shell_complexity: 4 branches for count query with optional table_id filter + error handling
def count_search_results(
    conn: sqlite3.Connection,
    query: str,
    table_id: str | None = None,
) -> Result[int, SearchError]:
    """Count total matching sayings for a search query."""
    if not query or not query.strip():
        return Success(0)

    try:
        if table_id:
            sql = """
                SELECT COUNT(*)
                FROM sayings_fts fts
                JOIN sayings s ON fts.rowid = s.rowid
                WHERE sayings_fts MATCH ? AND s.table_id = ?
            """
            params: tuple[object, ...] = (query, table_id)
        else:
            sql = """
                SELECT COUNT(*)
                FROM sayings_fts fts
                JOIN sayings s ON fts.rowid = s.rowid
                WHERE sayings_fts MATCH ?
            """
            params = (query,)

        row = conn.execute(sql, params).fetchone()
        return Success(int(row[0]) if row else 0)

    except sqlite3.Error as e:
        return Failure(SearchError(f"Database error: {e}"))


def rebuild_fts_index(conn: sqlite3.Connection) -> Result[int, SearchError]:
    """Rebuild the FTS5 index from the sayings table."""
    try:
        row = conn.execute("SELECT COUNT(*) FROM sayings").fetchone()
        count = int(row[0]) if row else 0
        conn.execute("INSERT INTO sayings_fts(sayings_fts) VALUES('rebuild')")
        conn.commit()
        return Success(count)
    except sqlite3.Error as e:
        conn.rollback()
        return Failure(SearchError(f"Failed to rebuild FTS index: {e}"))


# @shell_orchestration: Private helper for DB row -> domain object conversion
def _row_to_search_result(row: tuple[Any, ...]) -> Result[SearchResult, SearchError]:
    """Convert a database row to a SearchResult."""
    saying = row_to_saying(row[:9])
    return cast(Result[SearchResult, SearchError], SearchResult(
        saying=saying,
        rank=float(row[9]),
        snippet=str(row[10]) if row[10] else saying.content[:200],
    ))


from tasca.shell.storage.table_search_repo import (  # noqa: E402
    TableSearchHit,
    _row_to_table_hit,
    count_table_search_results,
    search_tables,
)

__all__ = [
    "SearchError",
    "SearchResult",
    "TableSearchHit",
    "_row_to_search_result",
    "_row_to_table_hit",
    "_truncate_snippet",
    "count_search_results",
    "count_table_search_results",
    "rebuild_fts_index",
    "search_sayings",
    "search_tables",
]
