from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import datetime

import pytest
from returns.result import Failure, Result, Success

from tasca.core.domain.saying import Saying, Speaker, SpeakerKind
from tasca.core.domain.table import Table, TableId, TableStatus, Version
from tasca.core.schema import create_sayings_table_ddl, create_tables_table_ddl
from tasca.shell.services.operations.table_control import (
    TableControlAction,
    TableControlErrorCode,
    build_control_content,
    execute_table_control,
    normalize_control_action,
)
from tasca.shell.storage.control_repo import ControlError
from tasca.shell.storage.table_repo import create_table


@pytest.fixture
def db_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.execute(create_tables_table_ddl())
    conn.execute(create_sayings_table_ddl())
    conn.commit()
    yield conn
    conn.close()


def _table(table_id: str, status: TableStatus, version: int = 1) -> Table:
    now = datetime(2024, 1, 1, 12, 0, 0)
    return Table(
        id=TableId(table_id),
        question="Question?",
        context=None,
        status=status,
        version=Version(version),
        created_at=now,
        updated_at=now,
    )


def _speaker() -> Speaker:
    return Speaker(kind=SpeakerKind.HUMAN, name="moderator")


def test_normalizes_action_and_control_content() -> None:
    assert normalize_control_action(" PAUSE ").unwrap() == TableControlAction.PAUSE
    assert build_control_content(TableControlAction.PAUSE, "  maintenance  ") == (
        "**CONTROL: PAUSE**\n\nReason: maintenance"
    )
    assert build_control_content(TableControlAction.RESUME, "  ") == "**CONTROL: RESUME**"


def test_invalid_action_is_typed_without_storage_mutation(db_conn: sqlite3.Connection) -> None:
    create_table(db_conn, _table("table-1", TableStatus.OPEN))

    result = execute_table_control(
        db_conn,
        table_id="table-1",
        action="archive",
        speaker=_speaker(),
        reason=None,
        now=datetime(2024, 1, 1, 12, 30, 0),
    )

    assert isinstance(result, Failure)
    assert result.failure().code == TableControlErrorCode.INVALID_ACTION
    assert result.failure().table_id == "table-1"
    assert db_conn.execute("SELECT COUNT(*) FROM sayings").fetchone()[0] == 0
    assert (
        db_conn.execute("SELECT status FROM tables WHERE id = ?", ("table-1",)).fetchone()[0]
        == "open"
    )


def test_missing_table_is_explicit_typed_failure(db_conn: sqlite3.Connection) -> None:
    result = execute_table_control(
        db_conn,
        table_id="missing",
        action="pause",
        speaker=_speaker(),
        reason=None,
    )

    assert isinstance(result, Failure)
    assert result.failure().code == TableControlErrorCode.TABLE_NOT_FOUND
    assert result.failure().table_id == "missing"


def test_invalid_transition_is_typed_and_does_not_append_control_saying(
    db_conn: sqlite3.Connection,
) -> None:
    create_table(db_conn, _table("table-1", TableStatus.CLOSED))

    result = execute_table_control(
        db_conn,
        table_id="table-1",
        action="pause",
        speaker=_speaker(),
        reason="should fail",
    )

    assert isinstance(result, Failure)
    error = result.failure()
    assert error.code == TableControlErrorCode.INVALID_TRANSITION
    assert error.current_status == TableStatus.CLOSED
    assert (
        db_conn.execute("SELECT COUNT(*) FROM sayings WHERE table_id = ?", ("table-1",)).fetchone()[
            0
        ]
        == 0
    )


def test_success_uses_atomic_storage_primitive_and_canonical_content(
    db_conn: sqlite3.Connection,
) -> None:
    create_table(db_conn, _table("table-1", TableStatus.OPEN))
    now = datetime(2024, 1, 1, 12, 30, 0)

    result = execute_table_control(
        db_conn,
        table_id="table-1",
        action="pause",
        speaker=_speaker(),
        reason="brief pause",
        now=now,
    )

    assert isinstance(result, Success)
    outcome = result.unwrap()
    assert outcome.table.status == TableStatus.PAUSED
    assert outcome.table.version == 2
    assert outcome.control_saying.sequence == 0
    assert outcome.control_content == "**CONTROL: PAUSE**\n\nReason: brief pause"
    row = db_conn.execute(
        "SELECT content FROM sayings WHERE table_id = ? AND sequence = ?",
        ("table-1", 0),
    ).fetchone()
    assert row[0] == outcome.control_content


def test_version_conflict_rolls_back_control_saying_and_preserves_actual_version(
    db_conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_table(db_conn, _table("table-1", TableStatus.OPEN))

    import tasca.shell.services.operations.table_control as operation_module

    original_atomic = operation_module.atomic_control_table

    def concurrent_update_then_atomic(
        conn: sqlite3.Connection,
        table_id: str,
        speaker: Speaker,
        control_content: str,
        new_status: TableStatus,
        current_table: Table,
        now: datetime,
    ) -> Result[tuple[Saying, Table], ControlError]:
        db_conn.execute(
            "UPDATE tables SET version = ?, status = ? WHERE id = ?",
            (5, TableStatus.PAUSED.value, "table-1"),
        )
        db_conn.commit()
        return original_atomic(
            conn=conn,
            table_id=table_id,
            speaker=speaker,
            control_content=control_content,
            new_status=new_status,
            current_table=current_table,
            now=now,
        )

    monkeypatch.setattr(operation_module, "atomic_control_table", concurrent_update_then_atomic)

    result = execute_table_control(
        db_conn,
        table_id="table-1",
        action="pause",
        speaker=_speaker(),
        reason="racing",
        now=datetime(2024, 1, 1, 12, 30, 0),
    )

    assert isinstance(result, Failure)
    error = result.failure()
    assert error.code == TableControlErrorCode.VERSION_CONFLICT
    assert error.expected_version == 1
    assert error.actual_version == 5
    assert error.current_status == TableStatus.PAUSED
    assert (
        db_conn.execute("SELECT COUNT(*) FROM sayings WHERE table_id = ?", ("table-1",)).fetchone()[
            0
        ]
        == 0
    )
    assert db_conn.execute(
        "SELECT status, version FROM tables WHERE id = ?", ("table-1",)
    ).fetchone() == (
        "paused",
        5,
    )
