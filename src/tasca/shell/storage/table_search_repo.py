"""Table-level search repository helpers.

This module contains the FTS + LIKE table search path split from
``search_repo`` so saying search and table search each remain below file-size
budget without changing the public storage API.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, cast

from returns.result import Failure, Result, Success

from tasca.core.storage_rows import truncate_snippet as _truncate_snippet
from tasca.shell.storage.search_repo import SearchError


@dataclass(frozen=True)
class TableSearchHit:
    """A table-level search hit with relevance ranking."""

    table_id: str
    question: str
    context: str | None
    status: str
    rank: float
    snippet: str
    match_type: str
    created_at: str
    updated_at: str


def _execute_fts_search(
    conn: sqlite3.Connection,
    query_param: str,
    status: str | None,
) -> Result[list[TableSearchHit], SearchError]:
    """Execute FTS5 search on saying content."""
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
    try:
        rows = conn.execute(fts_sql, params).fetchall()
    except sqlite3.Error as exc:
        return Failure(SearchError(f"Database error: {exc}"))

    return _rows_to_table_hits(rows)


def _rows_to_table_hits(rows: list[tuple[Any, ...]]) -> Result[list[TableSearchHit], SearchError]:
    """Convert SQL rows to table hits, preserving first malformed-row failure."""
    hits: list[TableSearchHit] = []
    for row in rows:
        try:
            hits.append(cast(TableSearchHit, _row_to_table_hit(row)))
        except (IndexError, TypeError, ValueError) as exc:
            return Failure(SearchError(f"Malformed table search row: {exc}"))
    return Success(hits)


def _execute_like_query(
    conn: sqlite3.Connection,
    query_param: str,
    status: str | None,
) -> Result[list[tuple[Any, ...]], SearchError]:
    """Execute LIKE SQL for question/context matching."""
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
    try:
        return Success(conn.execute(like_sql, params).fetchall())
    except sqlite3.Error as exc:
        return Failure(SearchError(f"Database error: {exc}"))


# @shell_orchestration: Row-shape normalization stays near SQL fallback path to preserve ordering semantics
def _build_like_hit(row: tuple[Any, ...], query_param: str) -> Result[TableSearchHit | None, SearchError]:
    """Build LIKE-based hit if row still semantically matches the query."""
    question = str(row[1]) if row[1] is not None else ""
    context = str(row[2]) if row[2] is not None else ""
    query_lower = query_param.lower()
    question_lower = question.lower()
    context_lower = context.lower()

    match = next(
        (
            (match_type, source_text)
            for match_type, source_text, matched in (
                ("question", question, query_lower in question_lower),
                ("context", context, bool(context) and query_lower in context_lower),
            )
            if matched
        ),
        None,
    )
    if match is None:
        return Success(None)
    match_type, source_text = match
    snippet = _truncate_snippet(source_text, query_param)

    return Success(TableSearchHit(
        table_id=str(row[0]),
        question=question,
        context=context,
        status=str(row[3]),
        rank=0.0,
        snippet=snippet,
        match_type=match_type,
        created_at=str(row[7]),
        updated_at=str(row[8]),
    ))


def _execute_like_search(
    conn: sqlite3.Connection,
    query_param: str,
    status: str | None,
    seen_tables: set[str],
) -> Result[list[TableSearchHit], SearchError]:
    """Execute LIKE search on table question and context."""
    rows_result = _execute_like_query(conn, query_param, status)
    if isinstance(rows_result, Failure):
        return rows_result
    return _collect_like_hits(rows_result.unwrap(), query_param, seen_tables)


def _collect_like_hits(
    rows: list[tuple[Any, ...]],
    query_param: str,
    seen_tables: set[str],
) -> Result[list[TableSearchHit], SearchError]:
    """Build deduplicated LIKE hits and update seen-table state."""
    hits: list[TableSearchHit] = []
    for row in rows:
        table_id = row[0]
        if table_id in seen_tables:
            continue

        hit = _build_like_hit(row, query_param).unwrap()
        if hit is None:
            continue

        hits.append(hit)
        seen_tables.add(table_id)

    return Success(hits)


# @shell_complexity: FTS query + LIKE fallback + status filter + pagination
def search_tables(
    conn: sqlite3.Connection,
    query: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Result[list[TableSearchHit], SearchError]:
    """Search tables using FTS5 for sayings and LIKE for question/context."""
    if not query or not query.strip():
        return Success([])

    try:
        query_param = query.strip()
        fts_result = _execute_fts_search(conn, query_param, status)
        if isinstance(fts_result, Failure):
            return fts_result
        fts_hits = fts_result.unwrap()

        seen_tables: set[str] = set()
        unique_fts_hits: list[TableSearchHit] = []
        for hit in fts_hits:
            if hit.table_id not in seen_tables:
                unique_fts_hits.append(hit)
                seen_tables.add(hit.table_id)

        like_result = _execute_like_search(conn, query_param, status, seen_tables)
        if isinstance(like_result, Failure):
            return like_result

        hits = unique_fts_hits + like_result.unwrap()
        return Success(hits[offset : offset + limit])

    except sqlite3.Error as e:
        error_msg = str(e).lower()
        if "fts5" in error_msg or "match" in error_msg or "syntax" in error_msg:
            return Failure(SearchError(f"Invalid search query syntax: {e}"))
        return Failure(SearchError(f"Database error: {e}"))


# @shell_complexity: 8 branches for count query with FTS + LIKE + status filter
def count_table_search_results(
    conn: sqlite3.Connection,
    query: str,
    status: str | None = None,
) -> Result[int, SearchError]:
    """Count total tables matching search query."""
    if not query or not query.strip():
        return Success(0)

    try:
        query_param = query.strip()
        fts_status_clause = "AND t.status = ?" if status else ""
        like_status_clause = "AND status = ?" if status else ""
        like_pattern = f"%{query_param}%"

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


def _row_to_table_hit(row: tuple[Any, ...]) -> Result[TableSearchHit, SearchError]:
    """Convert a database row to a TableSearchHit."""
    question = str(row[1] or "")
    snippet = str(row[5] or question[:200])
    return cast(Result[TableSearchHit, SearchError], TableSearchHit(
        table_id=str(row[0]),
        question=question,
        context=str(row[2]) if row[2] is not None else None,
        status=str(row[3]),
        rank=float(row[4]),
        snippet=snippet,
        match_type=str(row[6]),
        created_at=str(row[7]),
        updated_at=str(row[8]),
    ))
