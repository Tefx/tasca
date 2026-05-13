"""
Search API routes.

Endpoints for searching tables and sayings.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from pydantic import BaseModel
from returns.result import Failure, Result, Success

from tasca.core.domain.table import TableStatus
from tasca.shell.api.deps import get_db
from tasca.shell.api.fastapi_compat import APIRouter, Depends, HTTPException, Query
from tasca.shell.storage.search_repo import count_table_search_results, search_tables

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================


class SearchHit(BaseModel):
    """A single search hit representing a matching table.

    Attributes:
        table_id: ID of the matching table.
        question: The table's question/title.
        status: Current status of the table.
        snippet: Text snippet showing the match context.
        match_type: What matched ('question', 'context', 'saying').
        created_at: When the table was created.
        updated_at: When the table was last updated.
    """

    table_id: str
    question: str
    status: str
    snippet: str
    match_type: str
    created_at: str
    updated_at: str


class SearchResponse(BaseModel):
    """Response model for search endpoint.

    Attributes:
        query: The original search query.
        total: Total number of matching tables.
        hits: List of search hits.
    """

    query: str
    total: int
    hits: list[SearchHit]


# =============================================================================
# GET /search - Search tables and sayings
# =============================================================================


# @shell_complexity: Search response assembly maps status, query, and count failures at HTTP boundary.
def _search_response(
    conn: sqlite3.Connection,
    q: str,
    table_status: str | None,
    limit: int,
    offset: int,
) -> Result[SearchResponse, HTTPException]:
    """Run table search and build its HTTP response model."""
    if table_status and table_status not in [s.value for s in TableStatus]:
        return Failure(HTTPException(
            status_code=400,
            detail=f"Invalid status: {table_status}. Must be one of: open, paused, closed",
        ))
    search_result = search_tables(conn, q, table_status, limit, offset)
    if isinstance(search_result, Failure):
        error = search_result.failure()
        status_code = 400 if "syntax" in error.lower() or "fts5" in error.lower() else 500
        detail = f"Invalid search query: {error}" if status_code == 400 else f"Search failed: {error}"
        return Failure(HTTPException(status_code=status_code, detail=detail))
    count_result = count_table_search_results(conn, q, table_status)
    if isinstance(count_result, Failure):
        return Failure(HTTPException(status_code=500, detail=f"Search count failed: {count_result.failure()}"))
    response_hits = [
        SearchHit(
            table_id=hit.table_id,
            question=hit.question,
            status=hit.status,
            snippet=hit.snippet,
            match_type=hit.match_type,
            created_at=hit.created_at,
            updated_at=hit.updated_at,
        )
        for hit in search_result.unwrap()
    ]
    return Success(SearchResponse(query=q, total=count_result.unwrap(), hits=response_hits))


@router.get("", response_model=SearchResponse)
async def search_endpoint(
    q: Annotated[str, Query(min_length=1, description="Search query string")],
    table_status: str | None = Query(
        None, alias="status", description="Filter by table status (open, paused, closed)"
    ),
    limit: Annotated[int, Query(ge=1, le=200, description="Max results to return")] = 50,
    offset: Annotated[int, Query(ge=0, description="Offset for pagination")] = 0,
    conn: sqlite3.Connection = Depends(get_db),
) -> SearchResponse:
    """Search tables and sayings for matching content."""
    result = _search_response(conn, q, table_status, limit, offset)
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()
