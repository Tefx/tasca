"""Transport-neutral table export orchestration.

Fetch and format selection are owned here; JSONL and Markdown string contracts
remain delegated to :mod:`tasca.core.export_service`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from returns.result import Failure, Result, Success

from tasca.core.domain.saying import Saying
from tasca.core.domain.table import Table, TableId
from tasca.core.export_service import generate_jsonl, generate_markdown
from tasca.shell.storage.saying_repo import (
    DEFAULT_EXPORT_MAX_BYTES,
    SayingExportSizeExceededError,
    list_all_sayings_by_table,
)
from tasca.shell.storage.table_repo import TableNotFoundError, get_table

ExportFormat = Literal["markdown", "jsonl"]
ExportStatus = Literal["ok", "invalid_format", "not_found", "limit_exceeded", "database_error"]
VALID_EXPORT_FORMATS: tuple[ExportFormat, ...] = ("markdown", "jsonl")


@dataclass(frozen=True)
class TableExportOperationResult:
    """Typed outcome for a table export request."""

    status: ExportStatus
    table_id: str
    format: str
    content: str | None = None
    filename: str | None = None
    table: Table | None = None
    sayings: list[Saying] = field(default_factory=list)
    error: str | None = None
    estimated_bytes: int | None = None
    max_bytes: int | None = None

    @property
    def is_success(self) -> bool:
        """Return whether export content was produced."""
        return self.status == "ok"


# @shell_complexity: 5 branches for format validation + table fetch + saying fetch + limit + format dispatch
def export_table(
    conn: sqlite3.Connection,
    table_id: str,
    format: str = "markdown",
    *,
    exported_at: str | None = None,
    max_bytes: int = DEFAULT_EXPORT_MAX_BYTES,
) -> Result[TableExportOperationResult, TableExportOperationResult]:
    """Fetch table state and select a core-owned export formatter."""
    if format not in VALID_EXPORT_FORMATS:
        return Failure(TableExportOperationResult(
            status="invalid_format",
            table_id=table_id,
            format=format,
            error=f"Unknown format: {format}. Supported formats: markdown, jsonl",
        ))

    table_result = get_table(conn, TableId(table_id))
    if isinstance(table_result, Failure):
        error = table_result.failure()
        if isinstance(error, TableNotFoundError):
            return Failure(TableExportOperationResult(
                status="not_found",
                table_id=table_id,
                format=format,
                error=f"Table not found: {table_id}",
            ))
        return Failure(TableExportOperationResult(
            status="database_error",
            table_id=table_id,
            format=format,
            error=f"Failed to get table: {error}",
        ))

    table = table_result.unwrap()
    sayings_result = list_all_sayings_by_table(conn, table_id, max_bytes=max_bytes)
    if isinstance(sayings_result, Failure):
        error = sayings_result.failure()
        if isinstance(error, SayingExportSizeExceededError):
            return Failure(TableExportOperationResult(
                status="limit_exceeded",
                table_id=table_id,
                format=format,
                table=table,
                error=str(error),
                estimated_bytes=error.estimated_bytes,
                max_bytes=error.max_bytes,
            ))
        return Failure(TableExportOperationResult(
            status="database_error",
            table_id=table_id,
            format=format,
            table=table,
            error=f"Failed to list sayings: {error}",
        ))

    sayings = sayings_result.unwrap()
    if format == "jsonl":
        export_time = exported_at or datetime.now(UTC).isoformat()
        content = generate_jsonl(table, sayings, export_time)
        filename = f"{table_id}.jsonl"
    else:
        content = generate_markdown(table, sayings)
        filename = f"{table_id}.md"

    return Success(TableExportOperationResult(
        status="ok",
        table_id=table_id,
        format=format,
        content=content,
        filename=filename,
        table=table,
        sayings=sayings,
    ))
