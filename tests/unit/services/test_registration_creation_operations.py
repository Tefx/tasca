"""Tests for transport-neutral patron and table creation operations."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from returns.result import Failure, Success

from tasca.shell.services.operations.patron_registration import (
    PatronCreateError,
    PatronIdempotencyError,
    PatronLookupError,
    register_patron,
)
from tasca.shell.services.operations.table_creation import (
    TableCreateError,
    create_discussion_table,
)
from tasca.shell.storage.database import apply_schema
from tasca.shell.storage.table_repo import TableDatabaseError, create_table


@pytest.fixture
def conn() -> Generator[sqlite3.Connection]:
    db = sqlite3.connect(":memory:")
    apply_schema(db)
    yield db
    db.close()


def test_register_patron_uses_explicit_dedup_and_display_name_compatibility(
    conn: sqlite3.Connection,
) -> None:
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    first = register_patron(
        conn,
        "Agent Ada",
        kind="agent",
        alias="ada",
        meta={"team": "core"},
        patron_id="patron-1",
        dedup_id="retry-1",
        now=now,
    )
    retry = register_patron(
        conn,
        "Changed Label",
        patron_id="patron-2",
        dedup_id="retry-1",
        now=now,
    )
    same_display_name = register_patron(conn, "Agent Ada", patron_id="patron-3")

    assert isinstance(first, Success)
    assert first.unwrap().is_new is True
    assert first.unwrap().patron.id == "patron-1"
    assert first.unwrap().patron.created_at == now
    assert first.unwrap().patron.alias == "ada"
    assert isinstance(retry, Success)
    assert retry.unwrap().is_new is False
    assert retry.unwrap().patron.id == "patron-1"
    assert isinstance(same_display_name, Success)
    assert same_display_name.unwrap().is_new is False
    assert same_display_name.unwrap().patron.id == "patron-1"
    assert same_display_name.unwrap().patron.kind == "agent"
    assert same_display_name.unwrap().patron.created_at == now


def test_register_patron_reports_lookup_and_create_errors() -> None:
    closed = sqlite3.connect(":memory:")
    closed.close()
    lookup = register_patron(closed, "Agent Ada")

    assert isinstance(lookup, Failure)
    assert isinstance(lookup.failure(), PatronLookupError | PatronCreateError)

    conn = sqlite3.connect(":memory:")
    apply_schema(conn)
    conn.close()
    create = register_patron(conn, "Agent Ada")

    assert isinstance(create, Failure)
    assert isinstance(create.failure(), PatronLookupError | PatronCreateError | PatronIdempotencyError)


def test_create_discussion_table_owns_id_timestamp_defaults(
    conn: sqlite3.Connection,
) -> None:
    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    result = create_discussion_table(
        conn,
        "What should we build?",
        context="Bounded context",
        creator_patron_id="patron-1",
        now=now,
    )

    assert isinstance(result, Success)
    table = result.unwrap().table
    assert table.question == "What should we build?"
    assert table.context == "Bounded context"
    assert table.status.value == "open"
    assert table.version == 1
    assert table.created_at == now
    assert table.updated_at == now
    assert table.creator_patron_id == "patron-1"
    assert result.unwrap().invite_code == table.id
    assert result.unwrap().web_url == f"/tables/{table.id}"
    assert result.unwrap().host_ids == ["patron-1"]
    assert result.unwrap().metadata == {}
    assert result.unwrap().policy == {"mode": None, "params": {}, "custom": {}}
    assert result.unwrap().board == {}


def test_create_discussion_table_accepts_mcp_shape_defaults(
    conn: sqlite3.Connection,
) -> None:
    result = create_discussion_table(
        conn,
        title="MCP title",
        created_by="creator-1",
        host_ids=["creator-1", "host-2"],
        metadata={"space": "foundation"},
        policy={"mode": "open", "params": {}, "custom": {"x": True}},
        board={"agenda": "ship"},
    )

    assert isinstance(result, Success)
    outcome = result.unwrap()
    assert outcome.table.question == "MCP title"
    assert outcome.table.creator_patron_id == "creator-1"
    assert outcome.host_ids == ["creator-1", "host-2"]
    assert outcome.metadata == {"space": "foundation"}
    assert outcome.policy == {"mode": "open", "params": {}, "custom": {"x": True}}
    assert outcome.board == {"agenda": "ship"}


def test_create_discussion_table_reports_repository_errors(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from returns.result import Failure

    from tasca.shell.services.operations import table_creation

    def fail_create_table(*_args: object) -> Failure[TableDatabaseError]:
        return Failure(TableDatabaseError("forced create failure"))

    monkeypatch.setattr(table_creation, "create_table", fail_create_table)
    result = create_discussion_table(conn, "After schema damage")

    assert isinstance(result, Failure)
    assert isinstance(result.failure(), TableCreateError)


def test_create_discussion_table_propagates_generated_id_collisions(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tasca.core.domain.table import Table, TableId, TableStatus, Version
    from tasca.shell.services import table_id_generator

    now = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    def fixed_id(_choice: object, suffix: int | None = None) -> str:
        return "fixed-id" if suffix is None else f"fixed-id-{suffix}"

    monkeypatch.setattr(table_id_generator, "MAX_ID_RETRIES", 1)
    monkeypatch.setattr(table_id_generator, "generate_human_readable_id", fixed_id)
    for table_id in ("fixed-id", "fixed-id-1"):
        create_table(
            conn,
            Table(
                id=TableId(table_id),
                question=table_id,
                context=None,
                status=TableStatus.OPEN,
                version=Version(1),
                created_at=now,
                updated_at=now,
            ),
        )

    result = create_discussion_table(conn, "No IDs left")

    assert isinstance(result, Failure)
