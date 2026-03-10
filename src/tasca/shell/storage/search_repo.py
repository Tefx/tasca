"""
Search repository - FTS5 full-text search implementation.

This module handles I/O operations for searching sayings using SQLite FTS5.
All database operations use Result[T, E] for error handling.

Escape Hatch Convention (shell_result):
    Repository functions perform database I/O and return Result[T, E].
    Use "repo I/O" as the escape reason for database operations.
"""

# @invar:allow file_size: FTS and LIKE fallback search paths remain co-located to preserve shared ranking, dedupe, and pagination semantics.

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import NewType

from returns.result import Failure, Result, Success

from tasca.core.domain.patron import PatronId
from tasca.core.domain.saying import Saying, SayingId, Speaker, SpeakerKind

# Type for repository errors
SearchError = NewType("SearchError", str)


@dataclass(frozen=True)
class SearchResult:
    """A search result with relevance score.

    Attributes:
        saying: The matching Saying.
        rank: FTS5 relevance score (lower is better, based on bm25).
        snippet: Context snippet around the match.
    """

    saying: Saying
    rank: float
    snippet: str


@dataclass(frozen=True)
class TableSearchHit:
    """A table-level search hit with relevance ranking.

    Attributes:
        table_id: ID of the matching table.
        question: The table's question/title.
        context: Optional table context.
        status: Current status of the table.
        rank: FTS5 relevance score (lower is better, based on bm25).
             For question/context matches, rank is 0.0 (no FTS ranking).
        snippet: Text snippet showing the match context.
        match_type: What matched ('question', 'context', 'saying').
        created_at: ISO format timestamp when the table was created.
        updated_at: ISO format timestamp when the table was last updated.
    """

    table_id: str
    question: str
    context: str | None
    status: str
    rank: float
    snippet: str
    match_type: str
    created_at: str
    updated_at: str


# @invar:allow shell_result: search_repo.py - repo I/O returns domain objects, not Result
# @shell_complexity: 4 branches for FTS query with optional table_id filter + error handling
def search_sayings(
    conn: sqlite3.Connection,
    query: str,
    table_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Result[list[SearchResult], SearchError]:
    """Search sayings using FTS5 full-text search.

    Uses SQLite FTS5 with BM25 ranking for relevance scoring.
    Returns matching sayings ordered by relevance.

    Args:
        conn: Database connection with FTS5 tables initialized.
        query: Search query string (FTS5 query syntax supported).
        table_id: Optional filter to search within a specific table.
        limit: Maximum number of results (default 50).
        offset: Offset for pagination (default 0).

    Returns:
        Success with list of SearchResult, or Failure with error.

    Note:
        FTS5 query syntax supports:
        - Plain words: "hello world"
        - Phrases: '"hello world"'
        - AND: "hello AND world"
        - OR: "hello OR world"
        - NOT: "hello NOT world"
        - Prefix: "hello*"
    """
    if not query or not query.strip():
        return Success([])

    try:
        # Build FTS query with optional table_id filter
        # Use bm25() for relevance ranking (lower score = better match)
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
            params: tuple = (query, table_id, limit, offset)
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

        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()

        results = [_row_to_search_result(row) for row in rows]
        return Success(results)

    except sqlite3.Error as e:
        error_msg = str(e).lower()
        # FTS5 query syntax errors
        if "fts5" in error_msg or "match" in error_msg or "syntax" in error_msg:
            return Failure(SearchError(f"Invalid search query syntax: {e}"))
        return Failure(SearchError(f"Database error: {e}"))


# @invar:allow shell_result: search_repo.py - repo I/O returns domain objects, not Result
# @shell_complexity: 4 branches for count query with optional table_id filter + error handling
def count_search_results(
    conn: sqlite3.Connection,
    query: str,
    table_id: str | None = None,
) -> Result[int, SearchError]:
    """Count total matching sayings for a search query.

    Args:
        conn: Database connection with FTS5 tables initialized.
        query: Search query string.
        table_id: Optional filter to search within a specific table.

    Returns:
        Success with count, or Failure with error.
    """
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
            params: tuple = (query, table_id)
        else:
            sql = """
                SELECT COUNT(*)
                FROM sayings_fts fts
                JOIN sayings s ON fts.rowid = s.rowid
                WHERE sayings_fts MATCH ?
            """
            params = (query,)

        cursor = conn.execute(sql, params)
        row = cursor.fetchone()
        count = int(row[0]) if row else 0
        return Success(count)

    except sqlite3.Error as e:
        return Failure(SearchError(f"Database error: {e}"))


# @invar:allow shell_result: search_repo.py - repo I/O returns domain objects, not Result
def rebuild_fts_index(conn: sqlite3.Connection) -> Result[int, SearchError]:
    """Rebuild the FTS5 index from the sayings table.

    This is useful after bulk inserts or if the index gets out of sync.
    The 'rebuild' command fully repopulates the FTS table.

    Args:
        conn: Database connection.

    Returns:
        Success with number of rows indexed, or Failure with error.
    """
    try:
        # Get count before rebuild
        cursor = conn.execute("SELECT COUNT(*) FROM sayings")
        row = cursor.fetchone()
        count = int(row[0]) if row else 0

        # Rebuild FTS index
        conn.execute("INSERT INTO sayings_fts(sayings_fts) VALUES('rebuild')")
        conn.commit()

        return Success(count)

    except sqlite3.Error as e:
        conn.rollback()
        return Failure(SearchError(f"Failed to rebuild FTS index: {e}"))


# @invar:allow shell_result: search_repo.py - repo helper returns raw rows for search
# @shell_orchestration: Private helper for DB row -> domain object conversion
def _row_to_search_result(row: tuple) -> SearchResult:
    """Convert a database row to a SearchResult.

    Args:
        row: Database row tuple with saying fields + rank + snippet.

    Returns:
        SearchResult with Saying, rank, and snippet.
    """
    (
        saying_id,
        table_id,
        sequence,
        speaker_kind,
        speaker_name,
        patron_id,
        content,
        pinned,
        created_at_str,
        rank,
        snippet,
    ) = row

    # Parse the ISO format datetime string
    created_at = datetime.fromisoformat(created_at_str)

    saying = Saying(
        id=SayingId(saying_id),
        table_id=table_id,
        sequence=sequence,
        speaker=Speaker(
            kind=SpeakerKind(speaker_kind),
            name=speaker_name,
            patron_id=PatronId(patron_id) if patron_id else None,
        ),
        content=content,
        pinned=bool(pinned),
        created_at=created_at,
    )

    return SearchResult(
        saying=saying,
        rank=float(rank),
        snippet=snippet or content[:200],
    )


# =============================================================================
# Table-level Search (for REST /search endpoint)
# =============================================================================
# NOTE: Table-search helpers remain co-located to preserve exact FTS-first then
# LIKE ordering and snippet selection semantics shared by search/count paths.


# @invar:allow shell_result: search_repo.py - repo helper orchestrates raw row retrieval for search_tables
def _execute_fts_search(
    conn: sqlite3.Connection,
    query_param: str,
    status: str | None,
) -> list[TableSearchHit]:
    """Execute FTS5 search on saying content.

    Args:
        conn: Database connection.
        query_param: Search query string.
        status: Optional status filter.

    Returns:
        List of TableSearchHit from FTS5 matches.
    """
    fts_status_clause = "AND t.status = ?" if status else ""
    fts_sql = f"""
        SELECT DISTINCT
            t.id as table_id,
            t.question,
            t.context,
            t.status,
            fts.rank,
            snippet(sayings_fts, -1, '...', '...', '...', 32) as snippet,
            'saying' as match_type,
            t.created_at,
            t.updated_at
        FROM sayings_fts fts
        JOIN sayings s ON fts.rowid = s.rowid
        JOIN tables t ON s.table_id = t.id
        WHERE sayings_fts MATCH ?
        {fts_status_clause}
        ORDER BY fts.rank
    """

    params = (query_param, status) if status else (query_param,)
    rows = conn.execute(fts_sql, params).fetchall()

    return [_row_to_table_hit(row) for row in rows]


# @invar:allow shell_result: search_repo.py - repo helper executes SQL and returns raw rows
def _execute_like_query(
    conn: sqlite3.Connection,
    query_param: str,
    status: str | None,
) -> list[tuple]:
    """Execute LIKE SQL for question/context matching.

    Args:
        conn: Database connection.
        query_param: Search query string.
        status: Optional status filter.

    Returns:
        Database rows for matching tables ordered by created_at DESC.
    """
    like_status_clause = "AND status = ?" if status else ""
    like_pattern = f"%{query_param}%"
    like_sql = f"""
        SELECT
            id as table_id,
            question,
            context,
            status,
            0.0 as rank,
            '' as snippet,
            '' as match_type,
            created_at,
            updated_at
        FROM tables
        WHERE (question LIKE ? OR context LIKE ?)
        {like_status_clause}
        ORDER BY created_at DESC
    """

    params = (like_pattern, like_pattern, status) if status else (like_pattern, like_pattern)
    return conn.execute(like_sql, params).fetchall()


# @invar:allow shell_result: search_repo.py - repo helper computes match_type/snippet without extra I/O
# @shell_orchestration: Row-shape normalization stays near SQL fallback path to preserve ordering semantics
def _build_like_hit(row: tuple, query_param: str) -> TableSearchHit | None:
    """Build LIKE-based hit if row still semantically matches the query.

    Args:
        row: LIKE query row tuple.
        query_param: Search query string used for case-insensitive matching.

    Returns:
        TableSearchHit for question/context match, or None when no match applies.
    """
    question = row[1] or ""
    context = row[2] or ""
    query_lower = query_param.lower()
    question_lower = question.lower()
    context_lower = context.lower()

    if query_lower in question_lower:
        match_type = "question"
        snippet = _truncate_snippet(question, query_param)
    elif context and query_lower in context_lower:
        match_type = "context"
        snippet = _truncate_snippet(context, query_param)
    else:
        return None

    return TableSearchHit(
        table_id=row[0],
        question=question,
        context=context,
        status=row[3],
        rank=0.0,
        snippet=snippet,
        match_type=match_type,
        created_at=row[7],
        updated_at=row[8],
    )


# @invar:allow shell_result: search_repo.py - repo helper orchestrates LIKE fallback for search_tables
def _execute_like_search(
    conn: sqlite3.Connection,
    query_param: str,
    status: str | None,
    seen_tables: set[str],
) -> list[TableSearchHit]:
    """Execute LIKE search on table question and context.

    Args:
        conn: Database connection.
        query_param: Search query string.
        status: Optional status filter.
        seen_tables: Set of already-seen table IDs (for deduplication).

    Returns:
        List of TableSearchHit from LIKE matches (excluding already seen).
    """
    rows = _execute_like_query(conn, query_param, status)

    hits: list[TableSearchHit] = []
    for row in rows:
        table_id = row[0]
        if table_id in seen_tables:
            continue

        hit = _build_like_hit(row, query_param)
        if hit is None:
            continue

        hits.append(hit)
        seen_tables.add(table_id)

    return hits


# @invar:allow shell_result: search_repo.py - repo I/O returns domain objects, not Result
# @shell_complexity: FTS query + LIKE fallback + status filter + pagination
def search_tables(
    conn: sqlite3.Connection,
    query: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Result[list[TableSearchHit], SearchError]:
    """Search tables using FTS5 for sayings and LIKE for question/context.

    Combines FTS5 full-text search on saying content with LIKE search
    on table question and context. Returns results ordered by relevance
    (FTS5 BM25 rank for saying matches, then question/context matches).

    Args:
        conn: Database connection with FTS5 tables initialized.
        query: Search query string.
        status: Optional filter by table status (open, paused, closed).
        limit: Maximum number of results (default 50).
        offset: Offset for pagination (default 0).

    Returns:
        Success with list of TableSearchHit ordered by relevance,
        or Failure with error.

    Note:
        Each table appears at most once in results. If a table matches
        in multiple places (question, context, saying), the highest
        priority match is: question > context > saying.
    """
    if not query or not query.strip():
        return Success([])

    try:
        query_param = query.strip()

        # Priority 1: FTS5 search on saying content (BM25 ranking)
        fts_hits = _execute_fts_search(conn, query_param, status)

        # Deduplicate FTS hits (same table may have multiple matching sayings)
        seen_tables: set[str] = set()
        unique_fts_hits: list[TableSearchHit] = []
        for hit in fts_hits:
            if hit.table_id not in seen_tables:
                unique_fts_hits.append(hit)
                seen_tables.add(hit.table_id)

        # Priority 2 & 3: LIKE search for question and context
        like_hits = _execute_like_search(conn, query_param, status, seen_tables)

        # Combine: FTS hits first (have rank), then LIKE hits
        hits = unique_fts_hits + like_hits

        # Apply pagination
        paginated_hits = hits[offset : offset + limit]

        return Success(paginated_hits)

    except sqlite3.Error as e:
        error_msg = str(e).lower()
        # FTS5 query syntax errors
        if "fts5" in error_msg or "match" in error_msg or "syntax" in error_msg:
            return Failure(SearchError(f"Invalid search query syntax: {e}"))
        return Failure(SearchError(f"Database error: {e}"))


# @invar:allow shell_result: search_repo.py - repo I/O returns domain objects, not Result
# @shell_complexity: 8 branches for count query with FTS + LIKE + status filter
def count_table_search_results(
    conn: sqlite3.Connection,
    query: str,
    status: str | None = None,
) -> Result[int, SearchError]:
    """Count total tables matching search query.

    Combines FTS5 and LIKE matching to count unique tables.

    Args:
        conn: Database connection with FTS5 tables initialized.
        query: Search query string.
        status: Optional filter by table status.

    Returns:
        Success with count of unique matching tables, or Failure with error.
    """
    if not query or not query.strip():
        return Success(0)

    try:
        query_param = query.strip()
        fts_status_clause = "AND t.status = ?" if status else ""
        like_status_clause = "AND status = ?" if status else ""
        like_pattern = f"%{query_param}%"

        # Count unique tables from FTS5 (saying content)
        fts_count_sql = f"""
            SELECT COUNT(DISTINCT s.table_id)
            FROM sayings_fts fts
            JOIN sayings s ON fts.rowid = s.rowid
            JOIN tables t ON s.table_id = t.id
            WHERE sayings_fts MATCH ?
            {fts_status_clause}
        """

        if status:
            fts_count = conn.execute(fts_count_sql, (query_param, status)).fetchone()[0]
        else:
            fts_count = conn.execute(fts_count_sql, (query_param,)).fetchone()[0]

        # Count unique tables from LIKE (question/context) that weren't in FTS results
        like_count_sql = f"""
            SELECT COUNT(DISTINCT id)
            FROM tables
            WHERE (question LIKE ? OR context LIKE ?)
            {like_status_clause}
            AND id NOT IN (
                SELECT DISTINCT s.table_id
                FROM sayings_fts fts
                JOIN sayings s ON fts.rowid = s.rowid
                WHERE sayings_fts MATCH ?
            )
        """

        if status:
            like_count = conn.execute(
                like_count_sql, (like_pattern, like_pattern, status, query_param)
            ).fetchone()[0]
        else:
            like_count = conn.execute(
                like_count_sql, (like_pattern, like_pattern, query_param)
            ).fetchone()[0]

        return Success(int(fts_count) + int(like_count))

    except sqlite3.Error as e:
        return Failure(SearchError(f"Database error: {e}"))


# @invar:allow shell_result: search_repo.py - repo helper returns raw rows for search
# @shell_orchestration: Private helper for DB row format conversion
def _row_to_table_hit(row: tuple) -> TableSearchHit:
    """Convert a database row to a TableSearchHit.

    Args:
        row: Database row tuple with table fields + rank + snippet + match_type.

    Returns:
        TableSearchHit with all fields populated.
    """
    (
        table_id,
        question,
        context,
        status,
        rank,
        snippet,
        match_type,
        created_at,
        updated_at,
    ) = row

    return TableSearchHit(
        table_id=table_id,
        question=question,
        context=context,
        status=status,
        rank=float(rank),
        snippet=snippet or (question[:200] if question else ""),
        match_type=match_type,
        created_at=created_at,
        updated_at=updated_at,
    )


# @invar:allow shell_result: search_repo.py - repo helper returns raw rows for search
# @shell_orchestration: Private helper for snippet truncation
# @shell_complexity: 4 branches for text truncation logic
def _truncate_snippet(text: str, query: str, max_len: int = 200) -> str:
    """Truncate text around query match for snippet display.

    Args:
        text: Full text to truncate.
        query: Search query to find in text.
        max_len: Maximum snippet length.

    Returns:
        Truncated snippet with ellipsis if needed.
    """
    if len(text) <= max_len:
        return text

    # Find the matching portion
    idx = text.lower().find(query.lower())
    if idx < 0:
        return text[:max_len] + "..."

    # Show context around the match
    start = max(0, idx - 50)
    end = min(len(text), idx + len(query) + 50)

    result = text[start:end]
    if start > 0:
        result = "..." + result
    if end < len(text):
        result = result + "..."

    return result
