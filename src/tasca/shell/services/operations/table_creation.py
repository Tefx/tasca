"""Transport-neutral table creation orchestration.

This shell module owns table ID generation, timestamping, default table state,
and persistence for REST/MCP callers. Transport adapters remain responsible for
auth, idempotency caches, logging, and response/error envelope shaping.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from returns.result import Failure, Result, Success

from tasca.core.domain.table import Table, TableStatus, Version
from tasca.shell.services.table_id_generator import TableIdGenerationError, generate_table_id
from tasca.shell.storage.table_repo import TableError, create_table


@dataclass(frozen=True)
class TableCreationOutcome:
    """Result payload for a transport-neutral table creation."""

    table: Table
    invite_code: str
    web_url: str
    host_ids: list[str]
    metadata: dict[str, Any]
    policy: dict[str, Any]
    board: dict[str, Any]


DEFAULT_TABLE_POLICY: dict[str, Any] = {"mode": None, "params": {}, "custom": {}}


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


def create_discussion_table(
    conn: sqlite3.Connection,
    question: str | None = None,
    *,
    title: str | None = None,
    context: str | None = None,
    creator_patron_id: str | None = None,
    created_by: str | None = None,
    host_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    board: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Result[TableCreationOutcome, TableCreationError]:
    """Create a discussion table with shared shell invariants applied.

    Args:
        conn: Database connection.
        question: Discussion question or topic (legacy alias for ``title``).
        title: MCP-spec table title; persisted as the current Table.question field.
        context: Optional background context.
        creator_patron_id: Legacy creator identity alias.
        created_by: MCP-spec creator identity; preferred over ``creator_patron_id``.
        host_ids: MCP-spec host list; defaults to the creator when present.
        metadata: MCP-spec arbitrary metadata; currently response-owned in foundation.
        policy: MCP-spec policy object; currently response-owned in foundation.
        board: MCP-spec board object; currently response-owned in foundation.
        now: Optional timestamp injection for deterministic tests/callers.

    Returns:
        ``Success(TableCreationOutcome)`` or a typed orchestration failure.

    Example:
        >>> import sqlite3
        >>> from returns.result import Success
        >>> from tasca.shell.storage.database import apply_schema
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> result = create_discussion_table(conn, title="Question?", context="Context")
        >>> isinstance(result, Success)
        True
        >>> result.unwrap().table.status.value
        'open'
        >>> result.unwrap().invite_code == result.unwrap().table.id
        True
        >>> conn.close()
    """
    resolved_question = title or question
    if resolved_question is None:
        return Failure(TableCreateError(
            Table(
                id="",
                question="",
                context=context,
                status=TableStatus.OPEN,
                version=Version(1),
                created_at=now if now is not None else datetime.now(UTC),
                updated_at=now if now is not None else datetime.now(UTC),
                creator_patron_id=created_by or creator_patron_id,
            ),
            TableError("Either title or question is required"),
        ))
    table_id_result = generate_table_id(conn)
    if isinstance(table_id_result, Failure):
        return Failure(TableIdSelectionError(table_id_result.failure()))

    timestamp = now if now is not None else datetime.now(UTC)
    effective_host_ids = host_ids
    resolved_creator = created_by or creator_patron_id
    if effective_host_ids is None:
        effective_host_ids = [resolved_creator] if resolved_creator is not None else []
    elif resolved_creator is not None and resolved_creator not in effective_host_ids:
        effective_host_ids = [resolved_creator, *effective_host_ids]

    table = Table(
        id=table_id_result.unwrap(),
        question=resolved_question,
        context=context,
        status=TableStatus.OPEN,
        version=Version(1),
        created_at=timestamp,
        updated_at=timestamp,
        creator_patron_id=resolved_creator,
        host_ids=effective_host_ids,
    )
    create_result = create_table(conn, table)
    if isinstance(create_result, Failure):
        return Failure(TableCreateError(table, create_result.failure()))

    created = create_result.unwrap()
    return Success(TableCreationOutcome(
        table=created,
        invite_code=created.id,
        web_url=f"/tables/{created.id}",
        host_ids=created.host_ids,
        metadata=metadata if metadata is not None else {},
        policy=policy if policy is not None else DEFAULT_TABLE_POLICY.copy(),
        board=board if board is not None else {},
    ))
