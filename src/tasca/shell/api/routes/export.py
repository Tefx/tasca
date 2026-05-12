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
from typing import TYPE_CHECKING

from tasca.shell.api.fastapi_compat import APIRouter, Depends, HTTPException, Query, status

if TYPE_CHECKING:
    from fastapi.responses import Response
else:
    from tasca.shell.api.fastapi_compat import Response

from returns.result import Success

from tasca.shell.api.deps import get_db
from tasca.shell.services.operations.table_export import export_table

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


# @invar:allow shell_result: HTTP adapter maps shared Result failures to HTTPException.
def _run_export_or_raise(
    conn: sqlite3.Connection,
    table_id: str,
    format: str,
) -> str:
    """Run shared export operation and map typed failures to HTTP errors."""
    result = export_table(conn, table_id, format)
    if isinstance(result, Success):
        content = result.unwrap().content
        assert content is not None
        return content
    failure = result.failure()
    if failure.status == "not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=failure.error)
    if failure.status == "limit_exceeded":
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=failure.error)
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=failure.error)


# =============================================================================
# Endpoints (Shell Layer - Orchestrate I/O + Core)
# =============================================================================


# @invar:allow entry_point_too_thick: export.py endpoints - JSONL/markdown export with docstrings, type hints, and error handling
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
    content = _run_export_or_raise(conn, table_id, "jsonl")
    return _build_export_response(content, f"{table_id}.jsonl", download)


# @invar:allow entry_point_too_thick: export.py endpoints - JSONL/markdown export with docstrings, type hints, and error handling
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
    content = _run_export_or_raise(conn, table_id, "markdown")
    return _build_export_response(content, f"{table_id}.md", download)
