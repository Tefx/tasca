"""Offline behavioral checks for the 0.1.32 attachment live-verification producer."""

from __future__ import annotations

import argparse
import importlib.util
import json
import stat
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


def test_remote_observation_has_fixed_target_and_required_rollback_fact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Remote readback runs only against the named VM and rejects missing retained rollback state."""
    monkeypatch.setenv("TASCA_ADMIN_TOKEN", "admin-fixture")
    observed = {
        "service_execstart_matches": True,
        "release_dir_exists": True,
        "release_python": "cpython:3.13",
        "wheel_sha256": "a" * 64,
        "producer_sha256": "b" * 64,
        "sqlite_integrity": "ok",
        "saying_attachments_table": True,
        "domain_table_count": 3,
        "test_data_rows": None,
        "loopback_8000": True,
        "rollback_0_1_31_ready": True,
        "caddy_active": True,
    }
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(observed), stderr="")

    monkeypatch.setattr(VERIFIER.subprocess, "run", fake_run)
    verifier = VERIFIER.AttachmentVerifier(arguments(tmp_path), lambda *_args, **_kwargs: (200, b"{}", {}))
    assert verifier.remote()["domain_table_count"] == 3
    command = commands[0]
    assert command[:4] == ["gcloud", "compute", "ssh", "tasca-mcp"]
    assert command[command.index("--project") + 1] == "rda-engineering"
    assert command[command.index("--zone") + 1] == "asia-southeast1-b"
    observed["rollback_0_1_31_ready"] = False
    with pytest.raises(VERIFIER.VerificationError, match="required release state"):
        verifier.remote()


def test_cleanup_proves_api_absence_and_sqlite_test_data_removal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cleanup uses the isolated table ID and blocks when its SQLite rows remain."""
    monkeypatch.setenv("TASCA_ADMIN_TOKEN", "admin-fixture")
    calls: list[tuple[str, str]] = []

    def transport(url: str, operation: str, **_kwargs: object) -> tuple[int, bytes, dict[str, str]]:
        calls.append((url, operation))
        return (204 if operation == "test table cleanup" else 404), b"", {}

    verifier = VERIFIER.AttachmentVerifier(arguments(tmp_path), transport)
    verifier.table_id = "isolated-table"
    verifier.report["runtime_before"] = {"domain_table_count": 2}
    monkeypatch.setattr(verifier, "remote", lambda table_id: {"test_data_rows": 0, "domain_table_count": 2})

    verifier.cleanup()

    assert [operation for _url, operation in calls] == ["test table cleanup", "test table cleanup absence"]
    assert verifier.report["cleanup"]["status"] == "verified"


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
