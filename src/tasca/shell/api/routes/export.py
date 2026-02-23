"""
Export API routes.

Endpoints for exporting tables in various formats (JSONL, Markdown).

Shell Layer Contract:
    - I/O: Database queries via repositories
    - Error handling: HTTPException with appropriate status codes
    - Delegates formatting to core layer (export_service)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from returns.result import Failure

from tasca.core.domain.table import TableId
from tasca.core.export_service import generate_jsonl, generate_markdown
from tasca.shell.api.deps import get_db
from tasca.shell.storage.saying_repo import list_sayings_by_table
from tasca.shell.storage.table_repo import TableNotFoundError, get_table

if TYPE_CHECKING:
    pass

router = APIRouter()


# =============================================================================
# Helper Functions (Shell Layer - I/O Only)
# =============================================================================


# @invar:allow shell_result: Helper raises HTTPException directly (no Result needed)
def _fetch_table_and_sayings(
    conn: sqlite3.Connection,
    table_id: str,
) -> tuple:
    """Fetch table and sayings from database.

    Args:
        conn: Database connection.
        table_id: UUID of the table.

    Returns:
        Tuple of (table, sayings).

    Raises:
        HTTPException: 404 if table not found.
        HTTPException: 500 if database operation fails.
    """
    # Get table
    table_result = get_table(conn, TableId(table_id))
    if isinstance(table_result, Failure):
        error = table_result.failure()
        if isinstance(error, TableNotFoundError):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Table not found: {table_id}",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get table: {error}",
        )

    table = table_result.unwrap()

    # Get sayings
    sayings_result = list_sayings_by_table(conn, table_id, since_sequence=-1, limit=10000)
    if isinstance(sayings_result, Failure):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get sayings: {sayings_result.failure()}",
        )

    sayings = sayings_result.unwrap()

    return table, sayings


# =============================================================================
# Endpoints (Shell Layer - Orchestrate I/O + Core)
# =============================================================================


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
@router.get("/jsonl", response_class=PlainTextResponse)
async def export_jsonl_endpoint(
    table_id: str,
    conn: sqlite3.Connection = Depends(get_db),
) -> str:
    """Export a table in JSONL format.

    JSONL format:
    - First line: export header with metadata
    - Second line: table snapshot
    - Following lines: sayings ordered by sequence

    Args:
        table_id: The table identifier.
        conn: Database connection (injected via dependency).

    Returns:
        JSONL string with export data.

    Raises:
        HTTPException: 404 if table not found.
        HTTPException: 500 if database operation fails.
    """
    table, sayings = _fetch_table_and_sayings(conn, table_id)
    exported_at = datetime.now(timezone.utc).isoformat()
    return generate_jsonl(table, sayings, exported_at)


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
@router.get("/markdown", response_class=PlainTextResponse)
async def export_markdown_endpoint(
    table_id: str,
    conn: sqlite3.Connection = Depends(get_db),
) -> str:
    """Export a table in Markdown format.

    Markdown format:
    - Title header with question
    - Metadata section (table_id, status, etc.)
    - Board section (placeholder)
    - Transcript with full saying content (no truncation)

    Args:
        table_id: The table identifier.
        conn: Database connection (injected via dependency).

    Returns:
        Markdown string with table content.

    Raises:
        HTTPException: 404 if table not found.
        HTTPException: 500 if database operation fails.
    """
    table, sayings = _fetch_table_and_sayings(conn, table_id)
    return generate_markdown(table, sayings)
