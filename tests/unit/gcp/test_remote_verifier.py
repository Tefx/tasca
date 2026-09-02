"""Workflow contracts for the tracked Viewer/Admin remote verifier."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

REPOSITORY = Path(__file__).parents[3]
VERIFIER_PATH = REPOSITORY / "scripts/gcp/verify_viewer_auth_remote.py"
SPEC = importlib.util.spec_from_file_location("tracked_viewer_auth_verifier", VERIFIER_PATH)
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


@pytest.fixture
def verifier_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, object]:
    """Install transport, TCP, Secret Manager, and service-log fakes for one workflow."""
    admin = "admin-fixture-opaque"
    viewer = "viewer-fixture-opaque"
    state_file = tmp_path / "viewer-auth-state.json"
    operations: list[tuple[str, str | None, str]] = []
    secret_names: list[str] = []
    tools = {"table_create", "table_get"}
    state = {"table_exists": True, "viewer_mode": "configured"}

    def fake_request(
        url: str,
        operation: str,
        *,
        token: str | None = None,
        method: str = "GET",
        body: bytes | None = None,
        headers: object = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        del headers
        operations.append((operation, token, method))
        if url.endswith("/api/v1/health"):
            return 200, b'{"version":"0.1.31"}', {}
        if url.endswith("/api/v1/ready") or url.endswith("/docs") or url.endswith("/openapi.json"):
            return 200, b"{}", {}
        if url.endswith("/") and "/mcp/" not in url:
            return 200, b'<script src="/assets/app.js"></script>', {}
        if url.endswith("/assets/app.js"):
            return 200, b"asset", {}
        if url.endswith("/api/v1/auth/validate"):
            role = "admin" if token == admin else "viewer" if token == viewer else None
            return (200, json.dumps({"role": role}).encode(), {}) if role else (401, b"", {})
        if url.endswith("/mcp/"):
            if token != admin:
                return 401, b"", {}
            payload = json.loads((body or b"{}").decode())
            rpc_method = payload["method"]
            if rpc_method == "notifications/initialized":
                return 202, b"", {"Mcp-Session-Id": "fixture-session"}
            if rpc_method == "tools/list":
                listed = [{"name": name} for name in sorted(tools)]
                return 200, json.dumps({"result": {"tools": listed}}).encode(), {"Mcp-Session-Id": "fixture-session"}
            if rpc_method == "tools/call":
                name = payload["params"]["name"]
                if name == "table_create":
                    content = {"data": {"id": "fixture-table"}}
                elif name == "table_get" and state["table_exists"]:
                    assert payload["params"]["arguments"] == {"table_id": "fixture-table"}
                    content = {"data": {"id": "fixture-table"}}
                else:
                    return 200, b'{"error":{"code":-1}}', {"Mcp-Session-Id": "fixture-session"}
                response = {"result": {"content": [{"type": "text", "text": json.dumps(content)}]}}
                return 200, json.dumps(response).encode(), {"Mcp-Session-Id": "fixture-session"}
            return 200, b'{"result":{}}', {"Mcp-Session-Id": "fixture-session"}
        if url.endswith("/api/v1/tables"):
            if method == "GET":
                if state["viewer_mode"] == "configured" and token is None:
                    return 401, b"", {}
                return (200, b"[]", {}) if token in {None, viewer, admin} else (401, b"", {})
            return (403, b"", {}) if token != admin else (201, b'{"id":"unexpected-rest-create"}', {})
        if url.endswith("/api/v1/tables/fixture-table"):
            if method == "DELETE":
                assert token == admin
                state["table_exists"] = False
                return 204, b"", {}
            if token != admin:
                return 401, b"", {}
            return (200, b'{"id":"fixture-table"}', {}) if state["table_exists"] else (404, b"", {})
        raise AssertionError(f"unexpected request: {operation} {method} {url}")

    def fake_subprocess(command: list[str], **_kwargs: object) -> SimpleNamespace:
        if command[1:4] == ["secrets", "versions", "access"]:
            secret_name = command[command.index("--secret") + 1]
            secret_names.append(secret_name)
            assert admin not in command and viewer not in command
            value = admin if secret_name == "tasca-admin-token" else viewer
            return SimpleNamespace(returncode=0, stdout=value + "\n", stderr="")
        assert command[:4] == ["gcloud", "compute", "ssh", "tasca-mcp"]
        assert "--project" in command and command[command.index("--project") + 1] == "rda-engineering"
        assert "--zone" in command and command[command.index("--zone") + 1] == "asia-southeast1-b"
        return SimpleNamespace(returncode=0, stdout="normal service log\n", stderr="")

    def refused_connection(_address: tuple[str, int], timeout: int) -> object:
        assert timeout == 5
        raise ConnectionRefusedError

    monkeypatch.setattr(VERIFIER, "request", fake_request)
    monkeypatch.setattr(VERIFIER.subprocess, "run", fake_subprocess)
    monkeypatch.setattr(VERIFIER.socket, "create_connection", refused_connection)
    monkeypatch.setenv("TASCA_VIEWER_AUTH_STATE_FILE", str(state_file))
    return {
        "admin": admin,
        "viewer": viewer,
        "operations": operations,
        "secret_names": secret_names,
        "tools": tools,
        "state": state,
        "state_file": state_file,
    }


def arguments(*, phase: str, viewer_mode: str = "configured") -> SimpleNamespace:
    """Provide parsed arguments for one phase without any credential value in argv."""
    return SimpleNamespace(
        project="rda-engineering",
        viewer_secret="tasca-viewer-token",
        admin_secret="tasca-admin-token",
        expected_version="0.1.31",
        check_direct_port="34.1.134.239:8000",
        viewer_mode=viewer_mode,
        prepare=phase == "prepare",
        cleanup=phase == "cleanup",
    )


def test_verifier_requires_certificate_valid_https_base_and_closed_direct_port() -> None:
    """Credential-bearing verification rejects HTTP, paths, private ports, and reachable TCP/8000."""
    assert VERIFIER.require_https_base_url("https://tasca.example") == "https://tasca.example"
    with pytest.raises(ValueError, match="HTTPS"):
        VERIFIER.require_https_base_url("http://tasca.example")
    with pytest.raises(ValueError, match="port 443"):
        VERIFIER.require_https_base_url("https://tasca.example:8000")
    with pytest.raises(ValueError, match="path"):
        VERIFIER.require_https_base_url("https://tasca.example/mcp/")
    with pytest.raises(ValueError, match="HOST:8000"):
        VERIFIER.require_direct_port_refusal("34.1.134.239:443")


def test_prepare_exercises_configured_rest_mcp_create_get_and_writes_token_free_receipt(
    verifier_environment: dict[str, object],
) -> None:
    """Prepare runs real transport shapes, requires both tools, and writes only bridge state."""
    receipt = VERIFIER.prepare(arguments(phase="prepare"), "https://tasca.example")

    state_file = verifier_environment["state_file"]
    assert isinstance(state_file, Path)
    persisted = json.loads(state_file.read_text())
    assert persisted == {
        "format": "tasca-viewer-auth-persistence-v1",
        "phase": "prepared",
        "base_url": "https://tasca.example",
        "expected_version": "0.1.31",
        "table_id": "fixture-table",
    }
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    operations = verifier_environment["operations"]
    assert isinstance(operations, list)
    labels = [operation[0] for operation in operations]
    assert {
        "unauthenticated REST read",
        "unauthenticated REST mutation",
        "Viewer REST read",
        "Viewer REST mutation",
        "unauthenticated MCP",
        "Viewer MCP",
        "Admin MCP tools/list",
        "Admin MCP table_create",
        "Admin MCP table_get",
    }.issubset(labels)
    assert receipt["phase"] == "prepared"
    assert receipt["mcp"] == "create-and-exact-get-verified"


def test_prepare_fails_when_tools_list_omits_table_get(
    verifier_environment: dict[str, object],
) -> None:
    """A usable MCP session is insufficient unless both create and exact-get tools exist."""
    tools = verifier_environment["tools"]
    assert isinstance(tools, set)
    tools.remove("table_get")

    with pytest.raises(RuntimeError, match="table_create or table_get"):
        VERIFIER.prepare(arguments(phase="prepare"), "https://tasca.example")


def test_public_rest_mode_also_requires_unauthenticated_mutation_denial(
    verifier_environment: dict[str, object],
) -> None:
    """Public read mode cannot make unauthenticated mutation successful."""
    admin = verifier_environment["admin"]
    state = verifier_environment["state"]
    assert isinstance(admin, str)
    assert isinstance(state, dict)
    state["viewer_mode"] = "public"
    VERIFIER.verify_rest_matrix("https://tasca.example", "public", admin, None)

    operations = verifier_environment["operations"]
    assert isinstance(operations, list)
    assert ("unauthenticated REST mutation", None, "POST") in operations


def test_public_prepare_proves_unauthenticated_mutation_denial_without_viewer_secret_access(
    verifier_environment: dict[str, object],
) -> None:
    """Public Viewer mode keeps reads public while preserving mutation denial and Admin MCP checks."""
    state = verifier_environment["state"]
    assert isinstance(state, dict)
    state["viewer_mode"] = "public"
    receipt = VERIFIER.prepare(arguments(phase="prepare", viewer_mode="public"), "https://tasca.example")

    assert receipt["rest_matrix"] == "public-verified"
    secret_names = verifier_environment["secret_names"]
    assert secret_names == ["tasca-admin-token"]
    operations = verifier_environment["operations"]
    assert isinstance(operations, list)
    assert ("unauthenticated REST mutation", None, "POST") in operations
    assert "Viewer MCP" not in [operation[0] for operation in operations]


def test_cleanup_requires_rollout_reapply_then_reads_exact_id_deletes_and_proves_absence(
    verifier_environment: dict[str, object],
) -> None:
    """Cleanup accepts only the rollout-marked prepare receipt and removes the exact MCP table."""
    VERIFIER.prepare(arguments(phase="prepare"), "https://tasca.example")
    state_file = verifier_environment["state_file"]
    assert isinstance(state_file, Path)
    state = json.loads(state_file.read_text())
    state["phase"] = "reapplied"
    state_file.write_text(json.dumps(state))
    os.chmod(state_file, 0o600)

    receipt = VERIFIER.cleanup(arguments(phase="cleanup"), "https://tasca.example")

    assert receipt["phase"] == "cleanup"
    assert receipt["cleanup"] == "verified"
    assert not state_file.exists()
    operations = verifier_environment["operations"]
    assert isinstance(operations, list)
    labels = [operation[0] for operation in operations]
    assert {
        "persistence MCP tools/list",
        "post-reapply Admin MCP table_get",
        "Admin REST cleanup",
        "Admin REST cleanup absence",
    }.issubset(labels)


def test_cleanup_rejects_reapplied_state_when_mcp_loses_a_required_tool(
    verifier_environment: dict[str, object],
) -> None:
    """Cleanup repeats the required-tool gate instead of trusting a stale prepare result."""
    VERIFIER.prepare(arguments(phase="prepare"), "https://tasca.example")
    state_file = verifier_environment["state_file"]
    tools = verifier_environment["tools"]
    assert isinstance(state_file, Path)
    assert isinstance(tools, set)
    state = json.loads(state_file.read_text())
    state["phase"] = "reapplied"
    state_file.write_text(json.dumps(state))
    os.chmod(state_file, 0o600)
    tools.remove("table_create")

    with pytest.raises(RuntimeError, match="persistence MCP tools/list omitted"):
        VERIFIER.cleanup(arguments(phase="cleanup"), "https://tasca.example")


def test_cleanup_rejects_unreapplied_prepare_receipt(
    verifier_environment: dict[str, object],
) -> None:
    """The read-only verifier cannot skip the rollout-owned restart/reapply phase."""
    VERIFIER.prepare(arguments(phase="prepare"), "https://tasca.example")

    with pytest.raises(RuntimeError, match="not marked"):
        VERIFIER.cleanup(arguments(phase="cleanup"), "https://tasca.example")


def test_exact_downstream_cleanup_cli_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """The accepted downstream command selects the tracked cleanup producer and no token argv."""
    captured: dict[str, object] = {}

    def fake_cleanup(args: SimpleNamespace, base_url: str) -> dict[str, str]:
        captured["args"] = args
        captured["base_url"] = base_url
        return {"phase": "cleanup"}

    monkeypatch.setattr(VERIFIER, "cleanup", fake_cleanup)
    monkeypatch.setenv("TASCA_HTTPS_BASE_URL", "https://witnessed.example")
    monkeypatch.setattr(
        "sys.argv",
        [
            "verify_viewer_auth_remote.py",
            "--project",
            "rda-engineering",
            "--viewer-secret",
            "tasca-viewer-token",
            "--admin-secret",
            "tasca-admin-token",
            "--expected-version",
            "0.1.31",
            "--check-direct-port",
            "34.1.134.239:8000",
            "--cleanup",
        ],
    )

    assert VERIFIER.main() == 0
    args = captured["args"]
    assert isinstance(args, argparse.Namespace)
    assert args.cleanup and not args.prepare
    assert captured["base_url"] == "https://witnessed.example"
