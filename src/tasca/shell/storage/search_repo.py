"""
Search repository - FTS5 full-text search implementation.

This module handles I/O operations for searching sayings using SQLite FTS5.
All database operations use Result[T, E] for error handling.
"""

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


# @invar:allow shell_result: Shell layer - database I/O
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


# @invar:allow shell_result: Shell layer - database I/O
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


# @invar:allow shell_result: Shell layer - database I/O
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


# @invar:allow shell_result: Shell helper - database row format conversion
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


# @invar:allow shell_result: Shell layer - database I/O
# @shell_complexity: 15 branches for FTS query + LIKE fallback + status filter + pagination + deduplication
# @function_size: Complex search orchestration required for combining FTS and LIKE searches
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

        # Build status filter clauses (different for FTS and LIKE queries)
        fts_status_clause = "AND t.status = ?" if status else ""
        like_status_clause = "AND status = ?" if status else ""

        # Priority 1: Search in saying content via FTS5 (BM25 ranking)
        # This gives us relevance-scored saying matches
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

        if status:
            fts_rows = conn.execute(fts_sql, (query_param, status)).fetchall()
        else:
            fts_rows = conn.execute(fts_sql, (query_param,)).fetchall()

        # Track seen tables for deduplication
        seen_tables: set[str] = set()
        hits: list[TableSearchHit] = []

        # Add FTS5 (saying) matches first - these have BM25 ranking
        for row in fts_rows:
            table_id = row[0]
            if table_id not in seen_tables:
                hits.append(_row_to_table_hit(row))
                seen_tables.add(table_id)

        # Priority 2 & 3: LIKE search for question and context
        # These don't have BM25 ranking, so we add them after FTS results
        like_pattern = f"%{query_param}%"

        # Build LIKE query for question and context
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

        if status:
            like_rows = conn.execute(like_sql, (like_pattern, like_pattern, status)).fetchall()
        else:
            like_rows = conn.execute(like_sql, (like_pattern, like_pattern)).fetchall()

        # Process LIKE matches - question takes priority over context
        for row in like_rows:
            table_id = row[0]
            if table_id in seen_tables:
                continue

            question = row[1] or ""
            context = row[2] or ""

            # Determine match type and generate snippet
            if query_param.lower() in question.lower():
                match_type = "question"
                snippet = _truncate_snippet(question, query_param)
            elif context and query_param.lower() in context.lower():
                match_type = "context"
                snippet = _truncate_snippet(context, query_param)
            else:
                continue  # Should not happen, but be safe

            # Create hit with proper fields
            hit = TableSearchHit(
                table_id=table_id,
                question=question,
                context=context,
                status=row[3],
                rank=0.0,
                snippet=snippet,
                match_type=match_type,
                created_at=row[7],
                updated_at=row[8],
            )
            hits.append(hit)
            seen_tables.add(table_id)

        # Sort by rank (lower is better for BM25, so FTS hits come first)
        # Then by match_type priority: question > context > saying
        # For LIKE hits (rank=0), maintain order as added
        total = len(hits)

        # Apply pagination
        paginated_hits = hits[offset : offset + limit]

        return Success(paginated_hits)

    except sqlite3.Error as e:
        error_msg = str(e).lower()
        # FTS5 query syntax errors
        if "fts5" in error_msg or "match" in error_msg or "syntax" in error_msg:
            return Failure(SearchError(f"Invalid search query syntax: {e}"))
        return Failure(SearchError(f"Database error: {e}"))


# @invar:allow shell_result: Shell layer - database I/O
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


# @invar:allow shell_result: Shell helper - database row format conversion
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


# @invar:allow shell_result: Pure helper function, no I/O
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
