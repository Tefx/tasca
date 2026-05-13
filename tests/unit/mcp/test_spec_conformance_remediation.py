"""Focused regressions for batched spec-conformance remediation B6-B9."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator

import pytest

from tasca.shell.mcp.server import (
    patron_register,
    table_control,
    table_create,
    table_delete_batch,
    table_get,
    table_listen,
    table_say,
    table_update,
)
from tasca.shell.storage.database import apply_schema


@pytest.fixture
def test_db() -> Generator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    apply_schema(conn)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def override_db(test_db: sqlite3.Connection) -> Generator[None]:
    from tasca.shell.mcp import database

    original_connection = database._mcp_db_connection
    database._mcp_db_connection = test_db
    yield
    database._mcp_db_connection = original_connection


def _register(name: str, alias: str | None = None) -> str:
    result = patron_register(display_name=name, alias=alias)
    assert result["ok"] is True
    return result["data"]["patron_id"]


def test_agent_table_say_requires_patron_id_and_error_details() -> None:
    table_id = table_create(title="B7 table")["data"]["table_id"]

    result = table_say(table_id=table_id, content="missing patron", speaker_kind="agent")

    assert result["ok"] is False
    assert result["error"] == {
        "code": "INVALID_REQUEST",
        "message": "patron_id is required when speaker_kind is 'agent'",
        "details": {"speaker_kind": "agent"},
    }
    assert table_listen(table_id=table_id, since_sequence=-1)["data"]["sayings"] == []


def test_table_say_ambiguous_mention_rejects_before_append() -> None:
    speaker_id = _register("Speaker")
    _register("First", alias="dupe")
    _register("Second", alias="dupe")
    table_id = table_create(title="B8 ambiguity", created_by=speaker_id)["data"]["table_id"]

    result = table_say(
        table_id=table_id,
        content="hello dupe",
        speaker_kind="agent",
        patron_id=speaker_id,
        mentions=["dupe"],
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "AMBIGUOUS_MENTION"
    assert result["error"]["details"]["ambiguous"][0]["handle"] == "dupe"
    assert table_listen(table_id=table_id, since_sequence=-1)["data"]["sayings"] == []


def test_table_say_default_65536_limit_rejects_before_append() -> None:
    speaker_id = _register("Limit Speaker")
    table_id = table_create(title="B8 limit", created_by=speaker_id)["data"]["table_id"]

    result = table_say(
        table_id=table_id,
        content="x" * 65537,
        speaker_kind="agent",
        patron_id=speaker_id,
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "LIMIT_EXCEEDED"
    assert result["error"]["details"]["limit"] == 65536
    assert table_listen(table_id=table_id, since_sequence=-1)["data"]["sayings"] == []


def test_unauthorized_control_and_update_do_not_mutate() -> None:
    creator_id = _register("Creator")
    intruder_id = _register("Intruder")
    table = table_create(title="B6 permissions", created_by=creator_id)["data"]
    table_id = table["table_id"]

    denied_control = table_control(
        table_id=table_id,
        action="pause",
        speaker_name="Intruder",
        patron_id=intruder_id,
    )
    denied_update = table_update(
        table_id=table_id,
        expected_version=1,
        patch={"question": "mutated"},
        speaker_name="Intruder",
        patron_id=intruder_id,
    )

    assert denied_control["ok"] is False
    assert denied_control["error"]["code"] == "PERMISSION_DENIED"
    assert denied_update["ok"] is False
    assert denied_update["error"]["code"] == "PERMISSION_DENIED"
    after = table_get(table_id)["data"]["table"]
    assert after["status"] == "open"
    assert after["version"] == 1
    assert after["question"] == "B6 permissions"
    assert table_listen(table_id=table_id, since_sequence=-1)["data"]["sayings"] == []


def test_creator_host_and_human_admin_authorization_paths() -> None:
    creator_id = _register("Creator Allowed")
    host_id = _register("Host Allowed")
    new_host_id = _register("Updated Host")

    table = table_create(
        title="B6 allowed paths",
        created_by=creator_id,
        host_ids=[creator_id, host_id],
    )["data"]
    table_id = table["table_id"]
    assert table_get(table_id)["data"]["table"]["host_ids"] == [creator_id, host_id]

    host_update = table_update(
        table_id=table_id,
        expected_version=1,
        patch={"question": "configured host updated"},
        speaker_name="Host Allowed",
        patron_id=host_id,
    )
    assert host_update["ok"] is True
    assert host_update["data"]["table"]["question"] == "configured host updated"

    creator_update = table_update(
        table_id=table_id,
        expected_version=2,
        patch={"question": "creator updated", "host_ids": [creator_id, new_host_id]},
        speaker_name="Creator Allowed",
        patron_id=creator_id,
    )
    assert creator_update["ok"] is True
    assert creator_update["data"]["table"]["host_ids"] == [creator_id, new_host_id]

    host_control = table_control(
        table_id=table_id,
        action="pause",
        speaker_name="Updated Host",
        patron_id=new_host_id,
    )
    assert host_control["ok"] is True
    assert host_control["data"]["table_status"] == "paused"

    human_control = table_control(
        table_id=table_id,
        action="resume",
        speaker_name="Admin",
        patron_id=None,
    )
    assert human_control["ok"] is True
    assert human_control["data"]["table_status"] == "open"

    after = table_get(table_id)["data"]["table"]
    assert after["status"] == "open"
    assert after["version"] == 5
    assert after["host_ids"] == [creator_id, new_host_id]


def test_batch_delete_success_contract_includes_count_and_failed() -> None:
    first = table_create(title="delete 1")["data"]["table_id"]
    second = table_create(title="delete 2")["data"]["table_id"]
    assert table_control(first, "close", "Admin")["ok"] is True
    assert table_control(second, "close", "Admin")["ok"] is True

    result = table_delete_batch([first, second])

    assert result["ok"] is True
    assert result["data"]["deleted_count"] == 2
    assert result["data"]["failed"] == []
    assert set(result["data"]["deleted_ids"]) == {first, second}
