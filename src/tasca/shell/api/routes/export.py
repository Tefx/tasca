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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from returns.result import Failure

from tasca.core.domain.table import TableId
from tasca.core.export_service import generate_jsonl, generate_markdown
from tasca.shell.api.deps import get_db
from tasca.shell.storage.saying_repo import list_all_sayings_by_table
from tasca.shell.storage.table_repo import TableNotFoundError, get_table

if TYPE_CHECKING:
    pass

router = APIRouter()


# =============================================================================
# Helper Functions (Shell Layer - I/O Only)
# =============================================================================


# @invar:allow shell_result: Returns FastAPI Response, not Result
# @shell_orchestration: FastAPI response assembly is HTTP-layer wiring, not reusable domain logic
def _build_export_response(
    content: str,
    filename: str,
    download: bool = False,
) -> Response:
    """Build export response with optional download header.

    Args:
        content: The exported content string.
        filename: Filename for download (without extension).
        download: If True, add Content-Disposition attachment header.

    Returns:
        Response with appropriate headers.
    """
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    # Use application/octet-stream for downloads to prevent browser from
    # rendering content inline (which can appear "stuck" for large files).
    # For non-download (API consumers), use text/plain for readability.
    media_type = "application/octet-stream" if download else "text/plain; charset=utf-8"

    return Response(
        content=content,
        media_type=media_type,
        headers=headers if headers else None,
    )


# @shell_complexity: 4 branches for table lookup + sayings fetch + size check + error paths
# @invar:allow shell_result: Helper raises HTTPException directly (no Result needed)
def _fetch_table_and_sayings(
    conn: sqlite3.Connection,
    table_id: str,
) -> tuple:
    """Fetch table and all sayings from database for export.

    Export fetches ALL sayings without count truncation.
    Memory safety is provided by max-bytes limit in the repository.

    Args:
        conn: Database connection.
        table_id: UUID of the table.

    Returns:
        Tuple of (table, sayings).

    Raises:
        HTTPException: 404 if table not found.
        HTTPException: 413 if table too large to export.
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

    # Get ALL sayings for export (no count truncation)
    sayings_result = list_all_sayings_by_table(conn, table_id)
    if isinstance(sayings_result, Failure):
        error_msg = str(sayings_result.failure())
        # Check if it's a size exceeded error
        if "Export size exceeded" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=error_msg,
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get sayings: {error_msg}",
        )

    sayings = sayings_result.unwrap()

    return table, sayings


# =============================================================================
# Endpoints (Shell Layer - Orchestrate I/O + Core)
# =============================================================================


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
@router.get("/jsonl")
async def export_jsonl_endpoint(
    table_id: str,
    download: bool = Query(default=False, description="Return as downloadable file"),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    """Export a table in JSONL format.

    JSONL format:
    - First line: export header with metadata
    - Second line: table snapshot
    - Following lines: sayings ordered by sequence

    Args:
        table_id: The table identifier.
        download: If True, add Content-Disposition attachment header.
        conn: Database connection (injected via dependency).

    Returns:
        JSONL response with export data.

    Raises:
        HTTPException: 404 if table not found.
        HTTPException: 500 if database operation fails.
    """
    table, sayings = _fetch_table_and_sayings(conn, table_id)
    exported_at = datetime.now(timezone.utc).isoformat()
    content = generate_jsonl(table, sayings, exported_at)
    return _build_export_response(content, f"{table_id}.jsonl", download)


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
@router.get("/markdown")
async def export_markdown_endpoint(
    table_id: str,
    download: bool = Query(default=False, description="Return as downloadable file"),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    """Export a table in Markdown format.

    Markdown format:
    - Title header with question
    - Metadata section (table_id, status, etc.)
    - Board section (placeholder)
    - Transcript with full saying content (no truncation)

    Args:
        table_id: The table identifier.
        download: If True, add Content-Disposition attachment header.
        conn: Database connection (injected via dependency).

    Returns:
        Markdown response with table content.

    Raises:
        HTTPException: 404 if table not found.
        HTTPException: 500 if database operation fails.
    """
    table, sayings = _fetch_table_and_sayings(conn, table_id)
    content = generate_markdown(table, sayings)
    return _build_export_response(content, f"{table_id}.md", download)
