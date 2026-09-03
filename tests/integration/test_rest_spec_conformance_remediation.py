"""Focused REST regressions for B1-B5 spec-shape remediation."""

from __future__ import annotations

import json
import sqlite3
import uuid

import httpx
import pytest

from tests.integration.conftest import TEST_ADMIN_TOKEN


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_ADMIN_TOKEN}"}


@pytest.mark.asyncio
async def test_rest_auth_and_validation_errors_use_standard_envelope(http_client) -> None:
    auth_response = await http_client.post("/api/v1/tables", json={"title": "blocked"})
    assert auth_response.status_code == 401
    assert auth_response.json()["error"] == {
        "code": "PermissionDenied",
        "message": "Invalid or missing token",
        "details": {},
    }

    validation_response = await http_client.post("/api/v1/tables", json={}, headers=_auth())
    assert validation_response.status_code == 400
    assert validation_response.json()["error"] == {
        "code": "InvalidRequest",
        "message": "Either title or question is required",
        "details": {},
    }


@pytest.mark.asyncio
async def test_rest_patron_create_get_shape_and_dedup(http_client) -> None:
    suffix = uuid.uuid4().hex
    payload = {"display_name": f"REST Patron {suffix}", "alias": f"alias-{suffix}", "dedup_id": f"dedup-{suffix}"}

    first = await http_client.post("/api/v1/patrons", json=payload)
    second = await http_client.post(
        "/api/v1/patrons",
        json={**payload, "display_name": f"Different {suffix}"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    first_data = first.json()
    second_data = second.json()
    assert first_data["patron_id"] == second_data["patron_id"]
    assert first_data["display_name"] == payload["display_name"]
    assert first_data["server_ts"]
    assert first_data["id"] == first_data["patron_id"]  # compatibility only

    fetched = await http_client.get(f"/api/v1/patrons/{first_data['patron_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["patron"] == {
        "patron_id": first_data["patron_id"],
        "display_name": payload["display_name"],
        "alias": payload["alias"],
        "meta": None,
    }


@pytest.mark.asyncio
async def test_rest_table_create_shape_and_dedup(http_client) -> None:
    suffix = uuid.uuid4().hex
    payload = {
        "title": f"REST table {suffix}",
        "created_by": f"creator-{suffix}",
        "host_ids": [f"creator-{suffix}", f"host-{suffix}"],
        "metadata": {"space": "rest"},
        "policy": {"mode": "review", "params": {}, "custom": {}},
        "board": {"agenda": ["shape"]},
        "dedup_id": f"table-dedup-{suffix}",
    }

    first = await http_client.post("/api/v1/tables", json=payload, headers=_auth())
    second = await http_client.post(
        "/api/v1/tables",
        json={**payload, "title": "ignored on dedup"},
        headers=_auth(),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    data = first.json()
    assert second.json()["table_id"] == data["table_id"]
    assert data["title"] == payload["title"]
    assert data["creator_id"] == payload["created_by"]
    assert data["created_by"] == payload["created_by"]
    assert data["host_ids"] == payload["host_ids"]
    assert data["invite_code"] == data["table_id"]
    assert data["web_url"] == f"/tables/{data['table_id']}"
    assert data["metadata"] == payload["metadata"]
    assert data["policy"] == payload["policy"]
    assert data["board"] == payload["board"]
    assert data["id"] == data["table_id"]  # compatibility only


@pytest.mark.asyncio
async def test_rest_table_join_route_returns_documented_initial_block(http_client) -> None:
    suffix = uuid.uuid4().hex
    create_response = await http_client.post(
        "/api/v1/tables",
        json={"title": f"REST join {suffix}", "created_by": f"creator-{suffix}"},
        headers=_auth(),
    )
    assert create_response.status_code == 200
    table_id = create_response.json()["table_id"]

    join_response = await http_client.post(
        "/api/v1/tables/join",
        json={"invite_code": table_id, "history_limit": 10, "history_max_bytes": 65536},
    )

    assert join_response.status_code == 200
    data = join_response.json()
    assert data["table"]["table_id"] == table_id
    assert data["sequence_latest"] == 0
    assert data["history_sequence"] == 0
    assert data["initial"] == {
        "sayings": [],
        "next_sequence": 0,
        "has_more_history": False,
    }


@pytest.mark.asyncio
async def test_rest_join_history_includes_ordered_attachment_metadata(http_client) -> None:
    suffix = uuid.uuid4().hex
    create_response = await http_client.post(
        "/api/v1/tables",
        json={"title": f"REST attachment history {suffix}"},
        headers=_auth(),
    )
    assert create_response.status_code == 200
    table_id = create_response.json()["table_id"]

    with_attachment = await http_client.post(
        f"/api/v1/tables/{table_id}/sayings",
        json={
            "speaker_name": "Alice",
            "content": "First",
            "attachments": [
                {"name": "empty.md", "content": ""},
                {"name": "notes.markdown", "content": "body-only-secret"},
            ],
        },
        headers=_auth(),
    )
    without_attachment = await http_client.post(
        f"/api/v1/tables/{table_id}/sayings",
        json={"speaker_name": "Bob", "content": "Second"},
        headers=_auth(),
    )
    assert with_attachment.status_code == 201
    assert without_attachment.status_code == 201

    join_response = await http_client.post(
        "/api/v1/tables/join",
        json={"table_id": table_id, "history_limit": 10, "history_max_bytes": 65536},
    )

    assert join_response.status_code == 200
    sayings = join_response.json()["initial"]["sayings"]
    assert [saying["sequence"] for saying in sayings] == [0, 1]
    assert sayings[0]["attachments"] == with_attachment.json()["attachments"]
    assert [item["name"] for item in sayings[0]["attachments"]] == [
        "empty.md",
        "notes.markdown",
    ]
    assert all("content" not in item for item in sayings[0]["attachments"])
    assert sayings[1]["attachments"] == []
    assert "body-only-secret" not in join_response.text


@pytest.mark.asyncio
async def test_rest_wait_timeout_zero_returns_existing_sayings(http_client) -> None:
    suffix = uuid.uuid4().hex
    create_response = await http_client.post(
        "/api/v1/tables",
        json={"title": f"REST wait {suffix}"},
        headers=_auth(),
    )
    assert create_response.status_code == 200
    table_id = create_response.json()["table_id"]
    say_response = await http_client.post(
        f"/api/v1/tables/{table_id}/sayings",
        json={"speaker_name": "Tester", "content": "existing saying"},
        headers=_auth(),
    )
    assert say_response.status_code == 201

    wait_response = await http_client.get(
        f"/api/v1/tables/{table_id}/sayings/wait?since_sequence=-1&timeout=0"
    )

    assert wait_response.status_code == 200
    data = wait_response.json()
    assert data["timeout"] is False
    assert len(data["sayings"]) == 1
    assert data["sayings"][0]["content"] == "existing saying"
    assert data["next_sequence"] == data["sayings"][0]["sequence"]


@pytest.mark.asyncio
async def test_rest_post_close_saying_returns_state_error_not_permission(http_client) -> None:
    suffix = uuid.uuid4().hex
    create_response = await http_client.post(
        "/api/v1/tables",
        json={"title": f"REST closed {suffix}"},
        headers=_auth(),
    )
    assert create_response.status_code == 200
    table_id = create_response.json()["table_id"]
    close_response = await http_client.post(
        f"/api/v1/tables/{table_id}/control",
        json={"action": "close", "speaker_name": "Admin"},
        headers=_auth(),
    )
    assert close_response.status_code == 200

    say_response = await http_client.post(
        f"/api/v1/tables/{table_id}/sayings",
        json={"speaker_name": "Tester", "content": "should fail"},
        headers=_auth(),
    )

    assert say_response.status_code == 409
    assert say_response.json()["error"] == {
        "code": "TableClosed",
        "message": "Cannot add saying to table with status 'closed'. Table must be OPEN or PAUSED.",
        "details": {"table_status": "closed"},
    }


def _create_legacy_db_without_canonical_table_columns(db_path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """CREATE TABLE tables (
                id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                context TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.commit()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_rest_table_create_migrates_legacy_db_for_canonical_fields(tmp_path, monkeypatch) -> None:
    import tasca.config as config_module
    from tasca.shell.api.app import create_app

    db_path = tmp_path / "legacy.db"
    _create_legacy_db_without_canonical_table_columns(db_path)
    monkeypatch.setattr(config_module.settings, "db_path", str(db_path))

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    suffix = uuid.uuid4().hex
    payload = {
        "title": f"Legacy REST table {suffix}",
        "created_by": f"legacy-creator-{suffix}",
        "metadata": {"migrated": True},
        "policy": {"mode": "review", "params": {}, "custom": {}},
        "board": {"agenda": ["migration"]},
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/tables", json=payload, headers=_auth())

    assert response.status_code == 200
    data = response.json()
    assert data["metadata"] == payload["metadata"]
    assert data["policy"] == payload["policy"]
    assert data["board"] == payload["board"]

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tables)").fetchall()}
        stored = conn.execute(
            "SELECT metadata, policy, board FROM tables WHERE id = ?",
            (data["table_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert {"creator_patron_id", "host_ids", "metadata", "policy", "board"} <= columns
    assert stored is not None
    assert json.loads(stored[0]) == payload["metadata"]
    assert json.loads(stored[1]) == payload["policy"]
    assert json.loads(stored[2]) == payload["board"]


@pytest.mark.asyncio
async def test_rest_table_control_replays_same_dedup_response(http_client) -> None:
    suffix = uuid.uuid4().hex
    create_response = await http_client.post(
        "/api/v1/tables",
        json={"title": f"REST control dedup {suffix}", "created_by": f"creator-{suffix}"},
        headers=_auth(),
    )
    assert create_response.status_code == 200
    table_id = create_response.json()["table_id"]
    payload = {"action": "close", "speaker_name": "Admin", "dedup_id": f"close-dedup-{suffix}"}

    first = await http_client.post(f"/api/v1/tables/{table_id}/control", json=payload, headers=_auth())
    second = await http_client.post(f"/api/v1/tables/{table_id}/control", json=payload, headers=_auth())

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
