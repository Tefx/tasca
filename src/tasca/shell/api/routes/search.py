"""
Search API routes.

Endpoints for searching tables and sayings.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from tasca.shell.api.fastapi_compat import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from returns.result import Failure, Success

from tasca.core.domain.table import TableStatus
from tasca.shell.api.deps import get_db
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


# @invar:allow entry_point_too_thick: search.py search_endpoint GET route with docstrings, type hints, and error handling
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
    """Search tables and sayings for matching content.

    Uses FTS5 full-text search for saying content with BM25 relevance ranking,
    and LIKE search for table question and context.

    Search priorities (each table appears once):
    1. Question matches (highest priority)
    2. Context matches
    3. Saying content matches (FTS5 with BM25 ranking)

    Args:
        q: Search query string (FTS5 syntax supported for saying search).
        table_status: Optional filter by table status.
        limit: Maximum number of results (1-200, default 50).
        offset: Offset for pagination.
        conn: Database connection (injected via dependency).

    Returns:
        SearchResponse with matching tables ordered by relevance.

    Raises:
        HTTPException: 400 if invalid status value or FTS5 query syntax.
        HTTPException: 500 if database operation fails.
    """
    # Validate table_status if provided
    if table_status and table_status not in [s.value for s in TableStatus]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status: {table_status}. Must be one of: open, paused, closed",
        )

    # Perform search using FTS5-based search_repo
    search_result = search_tables(conn, q, table_status, limit, offset)

    if isinstance(search_result, Failure):
        error = search_result.failure()
        # Check for FTS5 syntax errors
        if "syntax" in error.lower() or "fts5" in error.lower():
            raise HTTPException(
                status_code=400,
                detail=f"Invalid search query: {error}",
            )
        raise HTTPException(
            status_code=500,
            detail=f"Search failed: {error}",
        )

    hits = search_result.unwrap()

    # Get total count
    count_result = count_table_search_results(conn, q, table_status)

    if isinstance(count_result, Failure):
        raise HTTPException(
            status_code=500,
            detail=f"Search count failed: {count_result.failure()}",
        )

    total = count_result.unwrap()

    # Convert TableSearchHit to SearchHit response model
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
        for hit in hits
    ]

    return SearchResponse(query=q, total=total, hits=response_hits)
