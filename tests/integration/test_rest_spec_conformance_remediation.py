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
