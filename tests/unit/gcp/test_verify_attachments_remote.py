"""Offline behavioral checks for the 0.1.32 attachment live-verification producer."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import io
import json
import shlex
import sqlite3
import stat
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPOSITORY = Path(__file__).parents[3]
VERIFIER_PATH = REPOSITORY / "scripts/gcp/verify_attachments_remote.py"
SPEC = importlib.util.spec_from_file_location("verify_attachments_remote", VERIFIER_PATH)
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


class StopAtJoin(RuntimeError):
    """Stop a fixture after the selected read credential reaches REST join."""


def arguments(tmp_path: Path, **changes: str) -> SimpleNamespace:
    """Create the exact live-step argument shape without a credential argument."""
    values = {
        "project": "rda-engineering",
        "zone": "asia-southeast1-b",
        "vm": "tasca-mcp",
        "host": "34.1.134.239.sslip.io",
        "expected_version": "0.1.32",
        "report_dir": str(tmp_path / "report"),
    }
    values.update(changes)
    return SimpleNamespace(**values)


def sqlite_baseline() -> dict[str, Any]:
    """Return a complete, internally consistent remote SQLite observation."""
    return {
        "device": 101,
        "inode": 202,
        "integrity_check": "ok",
        "foreign_key_check": 0,
        "table_ids": ["table-a", "table-b"],
        "counts": {
            "tables": 2,
            "sayings": 37,
            "sayings_fts": 37,
            "seats": 9,
            "saying_attachments": 0,
            "idempotency_keys": 67,
        },
    }


def runtime_observation(*, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the fixed-target observation shape emitted by the remote producer."""
    return {
        "service_execstart_matches": True,
        "release_dir_exists": True,
        "release_python": "cpython:3.13",
        "wheel_sha256": "a" * 64,
        "producer_sha256": "b" * 64,
        "sqlite_integrity": "ok",
        "saying_attachments_table": True,
        "sqlite": copy.deepcopy(baseline or sqlite_baseline()),
        "loopback_8000": True,
        "rollback_0_1_31_ready": True,
        "caddy_active": True,
    }


class FakeMcpTransport:
    """An in-memory authenticated Streamable HTTP seam for the tool inventory."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.requests: list[dict[str, Any]] = []

    def __call__(
        self,
        url: str,
        operation: str,
        *,
        token: str | None = None,
        method: str = "GET",
        body: bytes | None = None,
        headers: object = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        self.requests.append({"url": url, "operation": operation, "token": token, "body": body or b""})
        assert url == "https://34.1.134.239.sslip.io/mcp/"
        assert method == "POST"
        assert token == self.token
        request = json.loads((body or b"{}").decode())
        rpc_method = request["method"]
        if rpc_method == "initialize":
            result: dict[str, Any] = {"serverInfo": {"version": "0.1.32"}}
        elif rpc_method == "notifications/initialized":
            return 202, b"", {"Mcp-Session-Id": "fixture"}
        elif rpc_method == "tools/list":
            result = {"tools": [{"name": name} for name in sorted(VERIFIER.EXPECTED_TOOLS)]}
        else:
            result = {"content": [{"type": "text", "text": json.dumps({"ok": True, "data": {"id": "saying-1", "attachments": [{"id": "attachment-1", "position": 0, "name": "notes.md", "media_type": "text/markdown", "byte_size": 4}]}})}]}
        return 200, json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode(), {"Mcp-Session-Id": "fixture"}


def test_mcp_inventory_is_exact_and_credential_stays_out_of_json_rpc_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The 18-tool check uses one in-memory Admin handoff only in Authorization headers."""
    token = "admin-fixture-opaque"
    monkeypatch.setenv("TASCA_ADMIN_TOKEN", token)
    transport = FakeMcpTransport(token)
    verifier = VERIFIER.AttachmentVerifier(arguments(tmp_path), transport)

    initialized = verifier.mcp(
        "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {}}, "initialize"
    )
    verifier.mcp("notifications/initialized", {}, "initialized", notification=True)
    listed = verifier.mcp("tools/list", {}, "tools/list")
    names = [item["name"] for item in listed["result"]["tools"]]
    saying = verifier.tool("table_say", {"table_id": "table-1"})

    assert initialized["result"]["serverInfo"]["version"] == "0.1.32"
    assert len(names) == 18 and set(names) == VERIFIER.EXPECTED_TOOLS
    assert isinstance(saying, dict) and saying["id"] == "saying-1"
    assert all(token not in item["body"].decode() for item in transport.requests)


def test_metadata_and_export_checks_reject_body_leaks_and_wrong_jsonl_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ordinary reads cannot contain bodies; export proof requires JSONL 0.2 and appendix order."""
    monkeypatch.setenv("TASCA_ADMIN_TOKEN", "admin-fixture")
    verifier = VERIFIER.AttachmentVerifier(arguments(tmp_path), lambda *_args, **_kwargs: (200, b"{}", {}))
    verifier.table_id = "table-1"
    with pytest.raises(VERIFIER.VerificationError, match="malformed or complete"):
        verifier.metadata([{"id": "a", "position": 0, "name": "x.md", "media_type": "text/markdown", "byte_size": 1, "content": "x"}], "history")
    valid_jsonl = "\n".join(
        json.dumps(value)
        for value in [
            {"type": "export_header", "export_version": "0.2", "exported_at": "now", "table_id": "table-1"},
            {"type": "table", "table": {"id": "table-1"}},
            {"type": "saying", "saying": {"attachments": [{"name": "notes.md", "content": "body"}]}},
        ]
    )
    markdown = "## Transcript\nbody\n\n## Attachments\nnotes.md\nbody\n"
    assert verifier.export_signature(valid_jsonl, markdown, ["notes.md"])
    with pytest.raises(VERIFIER.VerificationError, match="format 0.2"):
        verifier.export_signature(valid_jsonl.replace('"0.2"', '"0.1"'), markdown, ["notes.md"])


def test_configured_viewer_read_credential_reaches_rest_history_join(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Configured Viewer mode authenticates the list and join history calls before any body read."""
    admin = "admin-fixture"
    viewer = "viewer-fixture"
    monkeypatch.setenv("TASCA_ADMIN_TOKEN", admin)
    monkeypatch.setenv("TASCA_VIEWER_TOKEN", viewer)
    verifier = VERIFIER.AttachmentVerifier(arguments(tmp_path), lambda *_args, **_kwargs: (200, b"{}", {}))
    history_calls: list[tuple[str, str | None]] = []
    create_payload: dict[str, Any] = {}

    def request(
        _url: str,
        operation: str,
        *,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        **_kwargs: object,
    ) -> dict[str, Any]:
        if operation == "REST test table create":
            assert token == admin
            assert payload is not None
            create_payload.update(payload)
            return {"table_id": "fixture-table"}
        if operation == "REST attachment create":
            assert token == admin
            return {
                "id": "rest-saying",
                "attachments": [
                    {"id": "rest-one", "position": 0, "name": "rest-one.md", "media_type": "text/markdown", "byte_size": 1},
                    {"id": "rest-two", "position": 1, "name": "rest-two.markdown", "media_type": "text/markdown", "byte_size": 1},
                ],
            }
        if operation == "REST list":
            history_calls.append((operation, token))
            assert token == viewer
            return {"sayings": []}
        if operation == "REST join":
            history_calls.append((operation, token))
            assert token == viewer
            raise StopAtJoin
        pytest.fail(f"unexpected request: {operation}")

    monkeypatch.setattr(verifier, "json_request", request)

    with pytest.raises(StopAtJoin):
        verifier.attachments("configured")

    assert history_calls == [("REST list", viewer), ("REST join", viewer)]
    assert create_payload["dedup_id"] == verifier.table_create_dedup_id


def test_remote_observation_has_fixed_target_and_complete_healthy_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Remote readback binds the VM, CPython 3.13, and every baseline component."""
    monkeypatch.setenv("TASCA_ADMIN_TOKEN", "admin-fixture")
    observed = runtime_observation()
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(observed), stderr="")

    monkeypatch.setattr(VERIFIER.subprocess, "run", fake_run)
    verifier = VERIFIER.AttachmentVerifier(arguments(tmp_path), lambda *_args, **_kwargs: (200, b"{}", {}))

    assert verifier.remote()["sqlite"] == sqlite_baseline()
    command = commands[0]
    assert command[:4] == ["gcloud", "compute", "ssh", "tasca-mcp"]
    assert command[command.index("--project") + 1] == "rda-engineering"
    assert command[command.index("--zone") + 1] == "asia-southeast1-b"
    remote_command = shlex.split(command[command.index("--command") + 1])
    assert remote_command[:3] == ["sudo", VERIFIER.REMOTE_PYTHON, "-c"]
    observed["sqlite"]["foreign_key_check"] = 1
    with pytest.raises(VERIFIER.VerificationError, match="healthy SQLite baseline"):
        verifier.remote()


def configure_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    verifier: Any,
    after: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    """Wire the cleanup workflow through a sessionless MCP fixture and offline remote seams."""
    verifier.table_id = "fixture-table"
    verifier.table_create_dedup_id = "fixture-dedup"
    verifier.report["runtime_before"] = runtime_observation()
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_mcp(
        method: str,
        params: dict[str, Any] | None,
        _operation: str,
        *,
        notification: bool = False,
    ) -> dict[str, Any]:
        if method == "initialize":
            verifier.session = "cleanup-session"
            return {"result": {"serverInfo": {"version": "0.1.32"}}}
        if notification:
            return {}
        assert method == "tools/call"
        assert params is not None
        name = params["name"]
        arguments = params["arguments"]
        calls.append((name, arguments))
        if name == "table_control":
            return {"result": {"structuredContent": {"ok": True, "data": {"table_status": "closed"}}}}
        if name == "table_delete_batch":
            return {"result": {"structuredContent": {"ok": True, "data": {"deleted_count": 1, "failed": [], "deleted_ids": ["fixture-table"]}}}}
        assert name == "table_get"
        return {"result": {"structuredContent": {"ok": False, "error": {"code": "NOT_FOUND"}}}}

    monkeypatch.setattr(verifier, "mcp", fake_mcp)
    monkeypatch.setattr(
        verifier,
        "remote_cleanup",
        lambda: {"idempotency_row_deleted": True, "test_domain_rows": {"tables": 0, "sayings": 0, "sayings_fts": 0, "seats": 0, "saying_attachments": 0}},
    )
    monkeypatch.setattr(verifier, "remote", lambda: after)
    return calls


def test_cleanup_establishes_session_then_closes_and_batch_deletes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cleanup after a pre-session failure uses MCP close, exact batch-delete, and MCP absence."""
    monkeypatch.setenv("TASCA_ADMIN_TOKEN", "admin-fixture")
    verifier = VERIFIER.AttachmentVerifier(
        arguments(tmp_path),
        lambda *_args, **_kwargs: pytest.fail("cleanup must not use the singleton REST DELETE route"),
    )
    after = runtime_observation()
    calls = configure_cleanup(monkeypatch, verifier, after)

    verifier.cleanup()

    assert calls == [
        (
            "table_control",
            {
                "table_id": "fixture-table",
                "action": "close",
                "speaker_name": "tasca-attachments-cleanup",
                "reason": "bounded verifier cleanup",
            },
        ),
        ("table_delete_batch", {"ids": ["fixture-table"]}),
        ("table_get", {"table_id": "fixture-table"}),
    ]
    assert verifier.report["cleanup"]["status"] == "verified"
    assert verifier.report["cleanup"]["absence"] == "MCP_NOT_FOUND"


def execute_remote_cleanup(command: list[str], database: Path) -> SimpleNamespace:
    """Execute the generated remote CPython source locally against one isolated SQLite fixture."""
    remote_command = shlex.split(command[command.index("--command") + 1])
    assert remote_command[:3] == ["sudo", VERIFIER.REMOTE_PYTHON, "-c"]
    source = remote_command[3].replace("'/var/lib/tasca/tasca.db'", repr(str(database)))
    stdout = io.StringIO()
    try:
        with redirect_stdout(stdout):
            exec(compile(source, "<remote-cleanup>", "exec"), {"__name__": "__main__"})
    except BaseException:
        return SimpleNamespace(returncode=1, stdout="", stderr="redacted")
    return SimpleNamespace(returncode=0, stdout=stdout.getvalue(), stderr="")


def create_cleanup_database(database: Path, rows: list[tuple[str, str, str]]) -> None:
    """Create just the tables the generated cleanup transaction must inspect."""
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE tables (id TEXT);
        CREATE TABLE sayings (id TEXT, table_id TEXT);
        CREATE TABLE sayings_fts (rowid INTEGER);
        CREATE TABLE seats (table_id TEXT);
        CREATE TABLE saying_attachments (saying_id TEXT);
        CREATE TABLE idempotency_keys (resource_key TEXT, tool_name TEXT, dedup_id TEXT);
        """
    )
    connection.executemany("INSERT INTO idempotency_keys VALUES (?, ?, ?)", rows)
    connection.commit()
    connection.close()


def test_remote_cleanup_executes_exact_transaction_and_preserves_other_idempotency_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The generated CPython transaction deletes one exact table-create row after BEGIN IMMEDIATE."""
    monkeypatch.setenv("TASCA_ADMIN_TOKEN", "admin-fixture")
    database = tmp_path / "tasca.db"
    target = ("table_create", "table_create", "fixture-dedup")
    other = ("table_create", "table_create", "other-dedup")
    create_cleanup_database(database, [target, other])
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return execute_remote_cleanup(command, database)

    monkeypatch.setattr(VERIFIER.subprocess, "run", fake_run)
    verifier = VERIFIER.AttachmentVerifier(arguments(tmp_path), lambda *_args, **_kwargs: (200, b"{}", {}))
    verifier.table_id = "fixture-table"
    verifier.table_create_dedup_id = "fixture-dedup"

    assert verifier.remote_cleanup()["idempotency_row_deleted"] is True
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT resource_key, tool_name, dedup_id FROM idempotency_keys").fetchall() == [other]
    connection.close()
    generated = shlex.split(commands[0][commands[0].index("--command") + 1])[3]
    assert "BEGIN IMMEDIATE" in generated


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [("table_create", "table_create", "fixture-dedup")] * 2,
        [("table_create", "table_create", "wrong-dedup")],
    ],
    ids=["zero", "multiple", "wrong"],
)
def test_remote_cleanup_rolls_back_for_nonexact_idempotency_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rows: list[tuple[str, str, str]]
) -> None:
    """Zero, multiple, or wrong idempotency rows cannot be deleted by the generated transaction."""
    monkeypatch.setenv("TASCA_ADMIN_TOKEN", "admin-fixture")
    database = tmp_path / "tasca.db"
    create_cleanup_database(database, rows)

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        return execute_remote_cleanup(command, database)

    monkeypatch.setattr(VERIFIER.subprocess, "run", fake_run)
    verifier = VERIFIER.AttachmentVerifier(arguments(tmp_path), lambda *_args, **_kwargs: (200, b"{}", {}))
    verifier.table_id = "fixture-table"
    verifier.table_create_dedup_id = "fixture-dedup"

    with pytest.raises(VERIFIER.CleanupBlocked, match="could not be reconciled"):
        verifier.remote_cleanup()

    connection = sqlite3.connect(database)
    assert connection.execute("SELECT resource_key, tool_name, dedup_id FROM idempotency_keys").fetchall() == rows
    connection.close()


def test_remote_cleanup_rolls_back_when_fixture_domain_rows_remain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The idempotency row is retained unless batch deletion removed every fixture domain row."""
    monkeypatch.setenv("TASCA_ADMIN_TOKEN", "admin-fixture")
    database = tmp_path / "tasca.db"
    target = ("table_create", "table_create", "fixture-dedup")
    create_cleanup_database(database, [target])
    connection = sqlite3.connect(database)
    connection.execute("INSERT INTO tables VALUES (?)", ("fixture-table",))
    connection.commit()
    connection.close()

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        return execute_remote_cleanup(command, database)

    monkeypatch.setattr(VERIFIER.subprocess, "run", fake_run)
    verifier = VERIFIER.AttachmentVerifier(arguments(tmp_path), lambda *_args, **_kwargs: (200, b"{}", {}))
    verifier.table_id = "fixture-table"
    verifier.table_create_dedup_id = "fixture-dedup"

    with pytest.raises(VERIFIER.CleanupBlocked, match="could not be reconciled"):
        verifier.remote_cleanup()

    connection = sqlite3.connect(database)
    assert connection.execute("SELECT resource_key, tool_name, dedup_id FROM idempotency_keys").fetchall() == [target]
    connection.close()


@pytest.mark.parametrize(
    "component",
    [
        "tables",
        "sayings",
        "sayings_fts",
        "seats",
        "saying_attachments",
        "idempotency_keys",
        "table_ids",
        "device",
        "inode",
        "integrity_check",
        "foreign_key_check",
    ],
)
def test_cleanup_blocks_each_incomplete_baseline_restoration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, component: str
) -> None:
    """Any count, ID set, file identity, integrity, or FK drift blocks cleanup completion."""
    monkeypatch.setenv("TASCA_ADMIN_TOKEN", "admin-fixture")
    after = runtime_observation()
    state = after["sqlite"]
    if component in state["counts"]:
        state["counts"][component] += 1
        if component == "tables":
            state["table_ids"].append("table-residue")
    elif component == "table_ids":
        state["table_ids"] = ["table-a", "table-residue"]
    elif component == "integrity_check":
        state[component] = "not ok"
    elif component == "foreign_key_check":
        state[component] = 1
    else:
        state[component] += 1
    verifier = VERIFIER.AttachmentVerifier(arguments(tmp_path), lambda *_args, **_kwargs: (200, b"{}", {}))
    configure_cleanup(monkeypatch, verifier, after)

    with pytest.raises(VERIFIER.CleanupBlocked, match="baseline"):
        verifier.cleanup()


def test_nonfinal_evidence_is_0600_and_browser_slots_remain_requirements(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Machine evidence contains no handoff token and never substitutes browser proof."""
    token = "admin-fixture-opaque"
    monkeypatch.setenv("TASCA_ADMIN_TOKEN", token)
    verifier = VERIFIER.AttachmentVerifier(arguments(tmp_path), lambda *_args, **_kwargs: (200, b"{}", {}))
    verifier.report["status"] = "MACHINE_CHECKS_PASS"

    path = verifier.write()

    assert path.name == "attachment-live-evidence.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert token not in path.read_text()
    assert verifier.report["format"] == "tasca.attachment-live-evidence.v1"
    assert verifier.report["status"] != "PASS"
    assert all(slot["status"] == "required" for slot in verifier.report["manual_browser_evidence"])


def test_binding_uses_last_tracked_file_commits_not_ambient_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Lifecycle-only commits cannot change the immutable producer evidence binding."""
    monkeypatch.setenv("TASCA_ADMIN_TOKEN", "admin-fixture")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        path = command[-1]
        value = "producer-commit" if path.endswith("attachment-forward-deploy.sh") else "verifier-commit"
        return SimpleNamespace(returncode=0, stdout=value + "\n", stderr="")

    monkeypatch.setattr(VERIFIER.subprocess, "run", fake_run)
    binding = VERIFIER.AttachmentVerifier(arguments(tmp_path), lambda *_args, **_kwargs: (200, b"{}", {})).binding()

    assert binding["forward_producer_commit"] == "producer-commit"
    assert binding["verifier_commit"] == "verifier-commit"
    assert all(command[3:6] == ["log", "-1", "--format=%H"] for command in commands)
    assert not any("rev-parse" in command for command in commands)


def test_cli_reports_machine_evidence_not_final_acceptance(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The CLI exposes a non-final evidence state and path for the later integration verifier."""
    expected = tmp_path / "attachment-live-evidence.json"

    class FakeVerifier:
        def __init__(self, _args: argparse.Namespace) -> None:
            pass

        def run(self) -> Path:
            return expected

    monkeypatch.setattr(VERIFIER, "AttachmentVerifier", FakeVerifier)
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_attachments_remote.py", "--project", "rda-engineering", "--zone", "asia-southeast1-b",
            "--vm", "tasca-mcp", "--host", "34.1.134.239.sslip.io", "--expected-version", "0.1.32",
            "--report-dir", str(tmp_path),
        ],
    )

    assert VERIFIER.main() == 0
    assert json.loads(capsys.readouterr().out) == {"status": "MACHINE_CHECKS_PASS", "evidence": str(expected)}


def test_wrong_target_rejects_before_runtime_credential_or_network_access(tmp_path: Path) -> None:
    """A plausible alternate target cannot reach credential, report, HTTP, or gcloud handling."""
    with pytest.raises(VERIFIER.VerificationError, match="authorized 0.1.32 target"):
        VERIFIER.AttachmentVerifier(arguments(tmp_path, project="wrong-project"))
    assert not tmp_path.exists() or not list(tmp_path.iterdir())
