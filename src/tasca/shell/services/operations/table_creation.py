"""Transport-neutral table creation orchestration.

This shell module owns table ID generation, timestamping, default table state,
and persistence for REST/MCP callers. Transport adapters remain responsible for
auth, idempotency caches, logging, and response/error envelope shaping.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from returns.result import Failure, Result, Success

from tasca.core.domain.table import Table, TableStatus, Version
from tasca.shell.services.table_id_generator import TableIdGenerationError, generate_table_id
from tasca.shell.storage.table_repo import TableError, create_table


@dataclass(frozen=True)
class TableCreationOutcome:
    """Result payload for a transport-neutral table creation."""

    table: Table


class TableCreationError(Exception):
    """Base typed error for table creation orchestration."""


class TableIdSelectionError(TableCreationError):
    """Unique table ID generation failed before persistence."""

    def __init__(self, cause: TableIdGenerationError) -> None:
        self.cause = cause
        super().__init__(f"Failed to generate table ID: {cause}")


class TableCreateError(TableCreationError):
    """Table persistence failed after creation data was prepared."""

    def __init__(self, table: Table, cause: TableError) -> None:
        self.table = table
        self.cause = cause
        super().__init__(f"Failed to create table {table.id}: {cause}")


# @invar:allow dead_export: extraction step publishes shared op before transport rewiring
def create_discussion_table(
    conn: sqlite3.Connection,
    question: str,
    *,
    context: str | None = None,
    creator_patron_id: str | None = None,
    now: datetime | None = None,
) -> Result[TableCreationOutcome, TableCreationError]:
    """Create a discussion table with shared shell invariants applied.

    Args:
        conn: Database connection.
        question: Discussion question or topic.
        context: Optional background context.
        creator_patron_id: Optional creator identity to persist with the table.
        now: Optional timestamp injection for deterministic tests/callers.

    Returns:
        ``Success(TableCreationOutcome)`` or a typed orchestration failure.

    Example:
        >>> import sqlite3
        >>> from returns.result import Success
        >>> from tasca.shell.storage.database import apply_schema
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> result = create_discussion_table(conn, "Question?", context="Context")
        >>> isinstance(result, Success)
        True
        >>> result.unwrap().table.status.value
        'open'
        >>> conn.close()
    """
    table_id_result = generate_table_id(conn)
    if isinstance(table_id_result, Failure):
        return Failure(TableIdSelectionError(table_id_result.failure()))

    timestamp = now if now is not None else datetime.now(UTC)
    table = Table(
        id=table_id_result.unwrap(),
        question=question,
        context=context,
        status=TableStatus.OPEN,
        version=Version(1),
        created_at=timestamp,
        updated_at=timestamp,
        creator_patron_id=creator_patron_id,
    )
    create_result = create_table(conn, table)
    if isinstance(create_result, Failure):
        return Failure(TableCreateError(table, create_result.failure()))

    return Success(TableCreationOutcome(table=create_result.unwrap()))
