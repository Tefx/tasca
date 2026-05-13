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
from typing import TYPE_CHECKING, cast

from tasca.shell.api.fastapi_compat import APIRouter, Depends, HTTPException, Query, status

if TYPE_CHECKING:
    from fastapi.responses import Response
else:
    from tasca.shell.api.fastapi_compat import Response

from returns.result import Failure, Result, Success

from tasca.shell.api.deps import get_db
from tasca.shell.services.operations.table_export import TableExportOperationResult, export_table

if TYPE_CHECKING:
    pass

router = APIRouter()


# =============================================================================
# Helper Functions (Shell Layer - I/O Only)
# =============================================================================


# @shell_orchestration: FastAPI response assembly is HTTP-layer wiring, not reusable domain logic
def _build_export_response(
    content: str,
    filename: str,
    download: bool = False,
) -> Result[Response, HTTPException]:
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

    return Success(Response(
        content=content,
        media_type=media_type,
        headers=headers if headers else None,
    ))


# @shell_orchestration: HTTP-layer mapping from shared export outcomes to FastAPI exceptions.
def _run_export_or_raise(
    conn: sqlite3.Connection,
    table_id: str,
    format: str,
) -> Result[TableExportOperationResult, HTTPException]:
    """Run shared export operation and map typed failures to HTTP errors."""
    result = export_table(conn, table_id, format)
    if isinstance(result, Success):
        return Success(cast(TableExportOperationResult, result.unwrap()))
    failure = result.failure()
    if failure.status == "not_found":
        return Failure(HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=failure.error))
    if failure.status == "limit_exceeded":
        return Failure(HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=failure.error))
    return Failure(HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=failure.error))


def _export_response_or_error(
    conn: sqlite3.Connection,
    table_id: str,
    format: str,
    download: bool,
) -> Result[Response, HTTPException]:
    """Run an export operation and build the matching HTTP response."""
    export_result = _run_export_or_raise(conn, table_id, format)
    if isinstance(export_result, Failure):
        return Failure(export_result.failure())
    export = export_result.unwrap()
    assert export.content is not None
    assert export.filename is not None
    return _build_export_response(export.content, export.filename, download)


# =============================================================================
# Endpoints (Shell Layer - Orchestrate I/O + Core)
# =============================================================================


@router.get("/jsonl")
async def export_jsonl_endpoint(
    table_id: str,
    download: bool = Query(default=False, description="Return as downloadable file"),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    """Export a table in JSONL format."""
    result = _export_response_or_error(conn, table_id, "jsonl", download)
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()


@router.get("/markdown")
async def export_markdown_endpoint(
    table_id: str,
    download: bool = Query(default=False, description="Return as downloadable file"),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    """Export a table in Markdown format."""
    result = _export_response_or_error(conn, table_id, "markdown", download)
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()
