"""
Export API routes.

Endpoints for exporting tables in various formats (JSONL, Markdown).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from io import StringIO
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from returns.result import Failure, Success

from tasca.core.domain.table import TableId
from tasca.shell.api.deps import get_db
from tasca.shell.storage.saying_repo import list_sayings_by_table
from tasca.shell.storage.table_repo import TableNotFoundError, get_table

if TYPE_CHECKING:
    pass

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================


class ExportHeader(BaseModel):
    """Header line for JSONL export.

    This is the first line in a JSONL export file, providing metadata
    about the export.

    Attributes:
        type: Always "export_header".
        export_version: Export format version.
        exported_at: ISO timestamp of when export was created.
        table_id: ID of the exported table.
    """

    type: str = "export_header"
    export_version: str = "0.1"
    exported_at: str
    table_id: str


class TableExport(BaseModel):
    """Table snapshot for export.

    Attributes:
        type: Always "table".
        table: The table data.
    """

    type: str = "table"
    table: dict


class SayingExport(BaseModel):
    """Saying entry for export.

    Attributes:
        type: Always "saying".
        saying: The saying data.
    """

    type: str = "saying"
    saying: dict


# =============================================================================
# Helper Functions
# =============================================================================


# @invar:allow shell_result: Helper raises HTTPException directly (no Result needed)
# @shell_complexity: 4 branches needed for table not found, sayings error, and formatting
def _generate_jsonl(
    conn: sqlite3.Connection,
    table_id: str,
) -> str:
    """Generate JSONL export for a table.

    Args:
        conn: Database connection.
        table_id: UUID of the table to export.

    Returns:
        JSONL string with export header, table snapshot, and sayings.

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

    # Build JSONL lines
    lines: list[str] = []

    # Header line
    header = ExportHeader(
        exported_at=datetime.now(timezone.utc).isoformat(),
        table_id=table_id,
    )
    lines.append(header.model_dump_json())

    # Table line
    table_export = TableExport(
        table={
            "id": table.id,
            "question": table.question,
            "context": table.context,
            "status": table.status.value,
            "version": table.version,
            "created_at": table.created_at.isoformat(),
            "updated_at": table.updated_at.isoformat(),
        }
    )
    lines.append(table_export.model_dump_json())

    # Sayings (ordered by sequence)
    for saying in sayings:
        saying_export = SayingExport(
            saying={
                "id": saying.id,
                "table_id": saying.table_id,
                "sequence": saying.sequence,
                "speaker": {
                    "kind": saying.speaker.kind.value,
                    "name": saying.speaker.name,
                    "patron_id": saying.speaker.patron_id,
                },
                "content": saying.content,
                "pinned": saying.pinned,
                "created_at": saying.created_at.isoformat(),
            }
        )
        lines.append(saying_export.model_dump_json())

    return "\n".join(lines)


# @invar:allow shell_result: Helper raises HTTPException directly (no Result needed)
# @shell_complexity: 7 branches needed for table not found, sayings error, and markdown formatting
def _generate_markdown(
    conn: sqlite3.Connection,
    table_id: str,
) -> str:
    """Generate Markdown export for a table.

    Args:
        conn: Database connection.
        table_id: UUID of the table to export.

    Returns:
        Markdown string with table metadata and transcript.

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

    # Build Markdown
    lines: list[str] = []

    # Title
    lines.append(f"# {table.question}")
    lines.append("")

    # Metadata
    lines.append(f"- table_id: {table.id}")
    lines.append(f"- status: {table.status.value}")
    lines.append(f"- version: {table.version}")
    lines.append(f"- created_at: {table.created_at.isoformat()}")
    lines.append(f"- updated_at: {table.updated_at.isoformat()}")
    if table.context:
        lines.append(f"- context: {table.context}")
    lines.append("")

    # Board section (placeholder - no board data yet)
    lines.append("## Board")
    lines.append("")
    lines.append("_No board data available._")
    lines.append("")

    # Transcript section
    lines.append("## Transcript")
    lines.append("")

    if not sayings:
        lines.append("_No sayings yet._")
    else:
        for saying in sayings:
            # Format: - [seq=N] TIMESTAMP (SPEAKER_KIND:NAME): content
            speaker_prefix = f"{saying.speaker.kind.value}:{saying.speaker.name}"
            timestamp = saying.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
            # Truncate long content for readability
            content = saying.content
            if len(content) > 200:
                content = content[:200] + "..."
            lines.append(f"- [seq={saying.sequence}] {timestamp} ({speaker_prefix}): {content}")

    return "\n".join(lines)


# =============================================================================
# GET /tables/{table_id}/export/jsonl - JSONL Export
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
    jsonl_content = _generate_jsonl(conn, table_id)
    return jsonl_content


# =============================================================================
# GET /tables/{table_id}/export/markdown - Markdown Export
# =============================================================================


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
    - Transcript with numbered sayings

    Args:
        table_id: The table identifier.
        conn: Database connection (injected via dependency).

    Returns:
        Markdown string with table content.

    Raises:
        HTTPException: 404 if table not found.
        HTTPException: 500 if database operation fails.
    """
    markdown_content = _generate_markdown(conn, table_id)
    return markdown_content
