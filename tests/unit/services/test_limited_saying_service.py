from __future__ import annotations

import sqlite3
from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from returns.result import Failure, Success

from tasca.core.domain.patron import Patron, PatronId
from tasca.core.domain.table import Table, TableId, TableStatus, TableUpdate, Version
from tasca.core.services.limits_service import LimitsConfig
from tasca.shell.services.limited_saying_service import (
    TableSayErrorKind,
    append_saying_operation,
)
from tasca.shell.storage.database import apply_schema
from tasca.shell.storage.patron_repo import create_patron
from tasca.shell.storage.table_repo import create_table, update_table


@pytest.fixture
def conn() -> Generator[sqlite3.Connection]:
    db = sqlite3.connect(":memory:", check_same_thread=False)
    apply_schema(db)
    yield db
    db.close()


def _create_table(conn: sqlite3.Connection, status: TableStatus = TableStatus.OPEN) -> str:
    table_id = TableId("table-1")
    now = datetime.now(UTC)
    table = Table(
        id=table_id,
        question="Question?",
        context=None,
        status=TableStatus.OPEN,
        version=Version(1),
        created_at=now,
        updated_at=now,
    )
    create_table(conn, table)
    if status != TableStatus.OPEN:
        update_table(
            conn,
            table_id,
            TableUpdate(question=table.question, context=table.context, status=status),
            Version(1),
            now,
        )
    return str(table_id)


def _create_patron(conn: sqlite3.Connection, patron_id: str = "patron-1") -> str:
    create_patron(
        conn,
        Patron(
            id=PatronId(patron_id),
            name="Helper Bot",
            kind="agent",
            created_at=datetime.now(UTC),
        ),
    )
    return patron_id


def test_append_saying_operation_owns_lookup_speaker_limits_and_append(
    conn: sqlite3.Connection,
) -> None:
    table_id = _create_table(conn)
    patron_id = _create_patron(conn)

    result = append_saying_operation(
        conn,
        table_id=table_id,
        content="Hello from shared operation",
        speaker_kind="agent",
        patron_id=patron_id,
        speaker_name=None,
        limits=LimitsConfig(max_content_length=100),
    )

    assert isinstance(result, Success)
    outcome = result.unwrap()
    assert outcome.speaker_key == patron_id
    assert outcome.saying.table_id == table_id
    assert outcome.saying.sequence == 0
    assert outcome.saying.speaker.name == "Helper Bot"


def test_append_saying_operation_classifies_closed_table(
    conn: sqlite3.Connection,
) -> None:
    table_id = _create_table(conn, TableStatus.CLOSED)

    result = append_saying_operation(
        conn,
        table_id=table_id,
        content="Should fail",
        speaker_kind="human",
        patron_id=None,
        speaker_name="Alice",
        limits=LimitsConfig(),
    )

    assert isinstance(result, Failure)
    error = result.failure()
    assert error.kind == TableSayErrorKind.OPERATION_NOT_ALLOWED
    assert error.table_status == "closed"


def test_append_saying_operation_classifies_speaker_constraint(
    conn: sqlite3.Connection,
) -> None:
    table_id = _create_table(conn)

    result = append_saying_operation(
        conn,
        table_id=table_id,
        content="Should fail",
        speaker_kind="human",
        patron_id="not-allowed",
        speaker_name="Alice",
        limits=LimitsConfig(),
    )

    assert isinstance(result, Failure)
    assert result.failure().kind == TableSayErrorKind.INVALID_SPEAKER


def test_append_saying_operation_classifies_limit_violation(
    conn: sqlite3.Connection,
) -> None:
    table_id = _create_table(conn)

    result = append_saying_operation(
        conn,
        table_id=table_id,
        content="too long",
        speaker_kind="human",
        patron_id=None,
        speaker_name="Alice",
        limits=LimitsConfig(max_content_length=3),
    )

    assert isinstance(result, Failure)
    error = result.failure()
    assert error.kind == TableSayErrorKind.LIMIT_EXCEEDED
    assert error.limit_error is not None
    assert error.limit_error.actual == len("too long")
