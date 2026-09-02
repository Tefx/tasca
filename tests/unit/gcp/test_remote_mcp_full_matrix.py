"""Offline behavioral checks for the public HTTPS MCP full-matrix verifier."""

from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path
from typing import Any, cast

import pytest

REPOSITORY = Path(__file__).parents[3]
VERIFIER_PATH = REPOSITORY / "scripts/gcp/verify_remote_mcp_full_matrix.py"
SPEC = importlib.util.spec_from_file_location("remote_mcp_full_matrix", VERIFIER_PATH)
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


class FakeTransport:
    """In-memory Streamable HTTP server with explicit table state."""

    def __init__(self, token: str = "admin-fixture-secret") -> None:
        self.token = token
        self.names = list(VERIFIER.EXPECTED_TOOLS)
        self.operations: list[dict[str, Any]] = []
        self.http_requests: list[dict[str, Any]] = []
        self.table: dict[str, Any] | None = None
        self.fail_tool: str | None = None
        self.fail_delete = False
        self.error_message = "fixture error"
        self.reference_seats = [
            {"patron_id": "patron-one", "state": "done"},
            {"patron_id": "patron-two", "state": "done"},
        ]
        self.patrons = {
            "patron-one": {"patron_id": "patron-one", "display_name": "Ada"},
            "patron-two": {"patron_id": "patron-two", "display_name": "Bea"},
        }
        self._failed = False

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
        self.http_requests.append(
            {
                "url": url,
                "operation": operation,
                "token": token,
                "method": method,
                "body": body or b"",
                "headers": headers,
            }
        )
        assert url.startswith("https://")
        if url.endswith("/api/v1/health"):
            return 200, b'{"version":"0.1.31","viewer_auth_required":true}', {}
        if url.endswith("/api/v1/auth/validate"):
            if token != self.token:
                return 401, b"", {}
            return 200, b'{"role":"admin"}', {}
        if not url.endswith("/mcp/"):
            raise AssertionError(f"unexpected URL: {url}")
        if token != self.token:
            return 401, b"", {}

        payload = json.loads((body or b"{}").decode())
        method_name = payload.get("method")
        if method_name == "initialize":
            return self._rpc(
                {"protocolVersion": "2025-03-26", "serverInfo": {"name": "tasca", "version": "0.1.31"}},
                session="fixture-session",
            )
        if method_name == "notifications/initialized":
            return 202, b"", {"Mcp-Session-Id": "fixture-session"}
        if method_name == "tools/list":
            return self._rpc(
                {"tools": [{"name": name} for name in self.names]},
                session="fixture-session",
            )
        if method_name != "tools/call":
            raise AssertionError(f"unexpected MCP method: {method_name}")

        params = payload["params"]
        name = params["name"]
        arguments = params.get("arguments", {})
        self.operations.append({"name": name, "arguments": arguments})
        if self.fail_tool == name and self.table is not None and not self._failed:
            self._failed = True
            return self._error("MATERIAL_FAILURE")
        return self._call_tool(name, arguments)

    def _rpc(self, data: dict[str, Any], *, session: str) -> tuple[int, bytes, dict[str, str]]:
        payload = {"jsonrpc": "2.0", "id": 1, "result": data}
        return 200, json.dumps(payload).encode(), {"Mcp-Session-Id": session}

    def _tool_payload(self, data: dict[str, Any]) -> tuple[int, bytes, dict[str, str]]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps({"ok": True, "data": data})}
                ]
            },
        }
        return 200, json.dumps(payload).encode(), {"Mcp-Session-Id": "fixture-session"}

    def _error(self, code: str) -> tuple[int, bytes, dict[str, str]]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "ok": False,
                                "error": {"code": code, "message": self.error_message},
                            }
                        ),
                    }
                ]
            },
        }
        return 200, json.dumps(payload).encode(), {"Mcp-Session-Id": "fixture-session"}

    def _table_data(self) -> dict[str, Any]:
        assert self.table is not None
        return {
            "id": self.table["id"],
            "table_id": self.table["id"],
            "title": "Tasca 0.1.31 full MCP matrix verification",
            "status": self.table["status"],
            "version": self.table["version"],
            "host_ids": ["patron-one", "patron-two"],
            "metadata": self.table["metadata"],
            "policy": {},
            "board": {},
        }

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[int, bytes, dict[str, str]]:
        if name == "seat_list":
            if arguments["table_id"] == "reference-table":
                return self._tool_payload({"seats": self.reference_seats, "active_count": 0})
            assert self.table is not None
            seats = [
                {"patron_id": patron_id, "state": state}
                for patron_id, state in self.table["seats"].items()
            ]
            active = [seat for seat in seats if seat["state"] in {"running", "idle"}]
            if arguments["active_only"]:
                seats = active
            return self._tool_payload({"seats": seats, "active_count": len(active)})
        if name == "patron_get":
            patron = self.patrons.get(arguments["patron_id"])
            return self._tool_payload({"patron": patron}) if patron else self._error("NOT_FOUND")
        if name == "patron_register":
            display_name = arguments["display_name"]
            patron = next(item for item in self.patrons.values() if item["display_name"] == display_name)
            return self._tool_payload({**patron, "is_new": False})
        if name == "table_create":
            self.table = {
                "id": "test-table",
                "status": "open",
                "version": 1,
                "metadata": arguments["metadata"],
                "seats": {},
                "sayings": [],
            }
            return self._tool_payload(self._table_data())
        if name == "table_join":
            if self.table is None:
                return self._error("NOT_FOUND")
            patron_id = arguments["patron_id"]
            self.table["seats"][patron_id] = "running"
            return self._tool_payload(
                {
                    "table": self._table_data(),
                    "initial": {"sayings": [], "next_sequence": 0, "has_more_history": False},
                    "seat": {"patron_id": patron_id, "state": "running"},
                }
            )
        if name == "table_get":
            if self.table is None or arguments["table_id"] != self.table["id"]:
                return self._error("NOT_FOUND")
            return self._tool_payload({"table": self._table_data()})
        if name == "table_list":
            tables = [] if self.table is None else [self._table_data()]
            return self._tool_payload({"tables": tables, "total_count": len(tables)})
        if name == "table_update":
            assert self.table is not None
            if arguments["expected_version"] != self.table["version"]:
                return self._error("VERSION_CONFLICT")
            self.table["version"] += 1
            self.table["metadata"] = arguments["patch"]["metadata"]
            return self._tool_payload({"table": self._table_data()})
        if name == "table_say":
            assert self.table is not None
            if self.table["status"] == "closed":
                return self._error("OPERATION_NOT_ALLOWED")
            saying = {
                "id": "saying-one",
                "saying_id": "saying-one",
                "sequence": len(self.table["sayings"]),
                "content": arguments["content"],
            }
            self.table["sayings"].append(saying)
            return self._tool_payload(saying)
        if name in {"table_listen", "table_wait"}:
            assert self.table is not None
            data: dict[str, Any] = {
                "sayings": self.table["sayings"],
                "next_sequence": len(self.table["sayings"]) - 1,
            }
            if name == "table_wait":
                data.update({"timeout": False, "table": self._table_data()})
            return self._tool_payload(data)
        if name == "table_export":
            assert self.table is not None
            if arguments["format"] == "markdown":
                content = "# matrix\n\nRepresentative full-matrix saying.\n"
            else:
                content = json.dumps({"table": self._table_data()}) + "\n"
            return self._tool_payload(
                {"content": content, "format": arguments["format"], "table_id": self.table["id"]}
            )
        if name == "seat_heartbeat":
            assert self.table is not None
            self.table["seats"][arguments["patron_id"]] = arguments["state"]
            return self._tool_payload({"expires_at": "2099-01-01T00:00:00+00:00"})
        if name == "table_control":
            if self.table is None:
                return self._error("NOT_FOUND")
            action = arguments["action"]
            if action == "pause" and self.table["status"] == "open":
                self.table["status"] = "paused"
            elif action == "resume" and self.table["status"] == "paused":
                self.table["status"] = "open"
            elif action == "close" and self.table["status"] in {"open", "paused"}:
                self.table["status"] = "closed"
            else:
                return self._error("INVALID_STATE")
            return self._tool_payload({"table_status": self.table["status"], "control_saying_sequence": 99})
        if name == "table_delete_batch":
            ids = arguments["ids"]
            if not ids:
                return self._error("INVALID_REQUEST")
            if self.fail_delete:
                return self._error("DATABASE_ERROR")
            if self.table is not None and ids == [self.table["id"]] and self.table["status"] == "closed":
                self.table = None
                return self._tool_payload({"deleted_count": 1, "failed": [], "deleted_ids": ids})
            return self._error("BATCH_PRECONDITION_FAILED")
        if name == "connect":
            assert arguments == {}
            return self._tool_payload({"mode": "local", "url": None, "has_token": False, "has_session": False})
        if name == "connection_status":
            assert arguments == {}
            return self._tool_payload({"mode": "local", "url": None, "is_healthy": True})
        raise AssertionError(f"unimplemented fixture tool: {name}")


def run_fixture(
    fake: FakeTransport,
    report_path: Path,
    *,
    wait_ms: int = 10,
) -> dict[str, Any]:
    """Run only through the injected transport and return the written report."""
    return cast(
        dict[str, Any],
        VERIFIER.run_verification(
            "https://tasca.example",
            "0.1.31",
            "reference-table",
            fake.token,
            report_path,
            wait_ms,
            transport=fake,
        ),
    )


def test_parse_mcp_response_accepts_json_and_sse_and_rejects_non_objects() -> None:
    assert VERIFIER.parse_mcp_response('{"jsonrpc":"2.0"}') == {"jsonrpc": "2.0"}
    assert VERIFIER.parse_mcp_response("event: message\ndata: {\"jsonrpc\":\"2.0\"}\n\n") == {
        "jsonrpc": "2.0"
    }
    with pytest.raises(ValueError, match="did not contain JSON"):
        VERIFIER.parse_mcp_response("event: message\n\n")
    with pytest.raises(ValueError, match="not an object"):
        VERIFIER.parse_mcp_response("[]")


def test_exact_inventory_rejects_missing_extra_and_duplicate_names() -> None:
    assert VERIFIER.assert_exact_tool_inventory(VERIFIER.EXPECTED_TOOLS) == VERIFIER.EXPECTED_TOOLS
    for altered in (
        [name for name in VERIFIER.EXPECTED_TOOLS if name != "table_wait"],
        [*VERIFIER.EXPECTED_TOOLS, "unexpected_tool"],
        [*VERIFIER.EXPECTED_TOOLS[:-1], VERIFIER.EXPECTED_TOOLS[-2]],
    ):
        with pytest.raises(VERIFIER.VerificationError, match="exact 17-tool inventory"):
            VERIFIER.assert_exact_tool_inventory(altered)


def test_preflight_gates_before_table_creation(tmp_path: Path) -> None:
    fake = FakeTransport()
    fake.names.remove("table_wait")
    report_path = tmp_path / "matrix.json"

    with pytest.raises(VERIFIER.VerificationError, match="exact 17-tool inventory"):
        run_fixture(fake, report_path)

    assert not any(operation["name"] == "table_create" for operation in fake.operations)
    report = json.loads(report_path.read_text())
    assert report["status"] == "FAIL"
    assert report["cleanup"]["status"] == "not_needed"


def test_preflight_requires_two_reusable_reference_patrons(tmp_path: Path) -> None:
    fake = FakeTransport()
    fake.reference_seats = [{"patron_id": "patron-one", "state": "done"}]

    with pytest.raises(VERIFIER.VerificationError, match="two distinct"):
        run_fixture(fake, tmp_path / "matrix.json")

    assert not any(operation["name"] == "table_create" for operation in fake.operations)


def test_full_matrix_report_and_cleanup_are_semantic_and_mode_0600(tmp_path: Path) -> None:
    fake = FakeTransport()
    report_path = tmp_path / "matrix.json"

    report = run_fixture(fake, report_path)

    assert report["status"] == "PASS"
    assert report["target"] == {
        "base_url": "https://tasca.example",
        "mcp_url": "https://tasca.example/mcp/",
        "expected_version": "0.1.31",
        "reference_table": "reference-table",
    }
    rows = report["tools"]
    assert len(rows) == 17
    assert {row["tool"] for row in rows} == set(VERIFIER.EXPECTED_TOOLS)
    assert all(row["status"] == "PASS" for row in rows)
    assert report["cleanup"]["status"] == "verified"
    assert report["cleanup"]["residual_table_id"] is None
    assert report["cleanup"]["post_delete"]["error_code"] == "NOT_FOUND"
    assert fake.table is None
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    assert VERIFIER.REPORT_FORMAT in report_path.read_text()
    assert fake.operations[-3:][0]["name"] == "table_control"
    assert [operation["name"] for operation in fake.operations[-3:]] == [
        "table_control",
        "table_delete_batch",
        "table_get",
    ]


def test_connect_is_empty_arguments_and_local_status_before_effects(tmp_path: Path) -> None:
    fake = FakeTransport()
    run_fixture(fake, tmp_path / "matrix.json")

    connect_calls = [operation for operation in fake.operations if operation["name"] == "connect"]
    status_calls = [
        operation for operation in fake.operations if operation["name"] == "connection_status"
    ]
    assert connect_calls == [{"name": "connect", "arguments": {}}]
    assert status_calls == [{"name": "connection_status", "arguments": {}}]
    connect_http = next(
        request for request in fake.http_requests if '"name":"connect"' in request["body"].decode()
    )
    body = connect_http["body"].decode()
    assert '"arguments":{}' in body
    assert '"token"' not in body
    assert fake.operations.index(connect_calls[0]) < [
        operation["name"] for operation in fake.operations
    ].index("table_create")


def test_seat_state_matrix_covers_all_active_mixed_and_all_left(tmp_path: Path) -> None:
    fake = FakeTransport()
    report = run_fixture(fake, tmp_path / "matrix.json")

    seat_rows = next(row for row in report["tools"] if row["tool"] == "seat_list")
    evidence = seat_rows["evidence"][-1]
    assert evidence["all_active"] == {"active_count": 2, "returned": 2}
    assert evidence["mixed"] == {"active_count": 1, "returned_active": 1, "returned_all": 2}
    assert evidence["all_left"] == {"active_count": 0, "returned_active": 0, "returned_all": 2}
    heartbeat_row = next(row for row in report["tools"] if row["tool"] == "seat_heartbeat")
    assert heartbeat_row["evidence"][-1]["states"] == ["running", "idle", "done"]

    table_seat_calls = [
        operation["arguments"]
        for operation in fake.operations
        if operation["name"] == "seat_list" and operation["arguments"]["table_id"] == "test-table"
    ]
    assert [arguments["active_only"] for arguments in table_seat_calls] == [
        True,
        False,
        True,
        False,
        True,
        False,
    ]


def test_fail_fast_after_creation_only_reconciles_exact_table(tmp_path: Path) -> None:
    fake = FakeTransport()
    fake.fail_tool = "table_update"
    report_path = tmp_path / "failed.json"

    with pytest.raises(VERIFIER.VerificationError, match="MATERIAL_FAILURE"):
        run_fixture(fake, report_path)

    names = [operation["name"] for operation in fake.operations]
    failure_index = names.index("table_update")
    assert names[failure_index:] == [
        "table_update",
        "table_control",
        "table_delete_batch",
        "table_get",
    ]
    report = json.loads(report_path.read_text())
    assert report["first_failure"]["operation"] == "MCP table_update"
    assert report["cleanup"]["status"] == "verified"
    assert report["cleanup"]["residual_table_id"] is None
    assert fake.table is None


def test_secret_is_redacted_from_failure_report_and_diagnostics(tmp_path: Path) -> None:
    fake = FakeTransport()
    fake.fail_tool = "table_update"
    fake.error_message = fake.token
    report_path = tmp_path / "redacted.json"

    with pytest.raises(VERIFIER.VerificationError):
        run_fixture(fake, report_path)

    report_text = report_path.read_text()
    assert fake.token not in report_text
    VERIFIER.assert_token_free(json.loads(report_text), fake.token)
    assert VERIFIER._redact_text(f"failure={fake.token}", fake.token) == "failure=<redacted>"


def test_cleanup_failure_reports_residual_id_and_blocks_completion(tmp_path: Path) -> None:
    fake = FakeTransport()
    fake.fail_tool = "table_update"
    fake.fail_delete = True
    report_path = tmp_path / "residual.json"

    with pytest.raises(VERIFIER.BlockedVerification, match="post-delete"):
        run_fixture(fake, report_path)

    report = json.loads(report_path.read_text())
    assert report["status"] == "BLOCKED"
    assert report["cleanup"]["status"] == "failed"
    assert report["cleanup"]["residual_table_id"] == "test-table"
    assert report["cleanup"]["post_delete"]["status"] == "failed"
    assert fake.table is not None


def test_write_report_rejects_credential_and_preserves_0600_contract(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    report = {"status": "PASS", "credential": "secret-fixture"}
    with pytest.raises(VERIFIER.VerificationError, match="credential"):
        VERIFIER.write_report(path, report, "secret-fixture")
    assert not path.exists()

    safe_path = tmp_path / "safe.json"
    VERIFIER.write_report(safe_path, {"status": "PASS"}, "secret-fixture")
    assert stat.S_IMODE(safe_path.stat().st_mode) == 0o600


def test_configuration_rejects_http_credentials_and_unbounded_wait() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        VERIFIER.require_https_base_url("http://tasca.example")
    with pytest.raises(ValueError, match="credentials"):
        VERIFIER.require_https_base_url("https://user:password@tasca.example")
    with pytest.raises(VERIFIER.VerificationError, match="wait bound"):
        VERIFIER.run_verification(
            "https://tasca.example",
            "0.1.31",
            "reference-table",
            "secret-fixture",
            wait_ms=VERIFIER.MAX_WAIT_MS + 1,
            transport=FakeTransport(),
        )
