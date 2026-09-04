#!/usr/bin/env python3
"""Produce secret-safe machine evidence for the Tasca 0.1.32 attachment release.

The verifier accepts no credential values or credential-location arguments.  Admin
and optional Viewer credentials enter only through the host process environment,
and browser-only observations remain explicit evidence slots rather than claims.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import socket
import stat
import subprocess
import sys
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

RELEASE_VERSION = "0.1.32"
PROJECT_ID = "rda-engineering"
ZONE = "asia-southeast1-b"
VM = "tasca-mcp"
HTTPS_HOST = "34.1.134.239.sslip.io"
REMOTE_PYTHON = "/var/lib/tasca/.local/share/uv/python/cpython-3.13.15-linux-x86_64-gnu/bin/python3.13"
RELEASE_DIR = f"/opt/tasca/releases/{RELEASE_VERSION}"
PREVIOUS_RELEASE_DIR = "/opt/tasca/releases/0.1.31"
STAGED_WHEEL = f"/var/tmp/tasca-attachments-0.1.32/tasca-{RELEASE_VERSION}-py3-none-any.whl"
REPORT_FORMAT = "tasca.attachment-live-evidence.v1"
MAX_REPORT_BYTES = 256 * 1024
EXPECTED_TOOLS = frozenset(
    {
        "patron_register", "patron_get", "table_create", "table_join", "table_get",
        "table_list", "table_delete_batch", "table_export", "table_say", "attachment_get",
        "table_listen", "table_control", "table_update", "table_wait", "seat_heartbeat",
        "seat_list", "connect", "connection_status",
    }
)


class VerificationError(RuntimeError):
    pass


class CleanupBlocked(VerificationError):
    pass


class AttachmentVerifier:

    def __init__(self, args: argparse.Namespace, transport: Any = None) -> None:
        if (args.project, args.zone, args.vm, args.host, args.expected_version) != (
            PROJECT_ID, ZONE, VM, HTTPS_HOST, RELEASE_VERSION
        ):
            raise VerificationError("verifier arguments do not match the authorized 0.1.32 target")
        if not args.host or "://" in args.host or any(value in args.host for value in "/?#@"):
            raise VerificationError("host must be a hostname without scheme, path, or credentials")
        parsed = urlsplit(f"https://{args.host}")
        if not parsed.hostname or parsed.port not in (None, 443):
            raise VerificationError("host must select HTTPS port 443")
        self.args = args
        self.base_url = urlunsplit(("https", parsed.netloc, "", "", ""))
        self.transport = transport or self._open
        self.admin = os.environ.get("TASCA_ADMIN_TOKEN")
        self.viewer = os.environ.get("TASCA_VIEWER_TOKEN") or None
        if not self.admin:
            raise VerificationError("required runtime Admin credential is unavailable")
        self.session: str | None = None
        self.request_id = 0
        self.table_id: str | None = None
        self.fixture_question: str | None = None
        self.table_create_dedup_id: str | None = None
        self.table_create_attempted = False
        self.table_create_idempotency_count: int | None = None
        self.table_say_dedup_id: str | None = None
        self.table_say_idempotency_count: int | None = 0
        self.report: dict[str, Any] = {
            "format": REPORT_FORMAT,
            "status": "MACHINE_CHECKS_FAIL",
            "target": {
                "project": args.project,
                "zone": args.zone,
                "vm": args.vm,
                "host": args.host,
                "expected_version": args.expected_version,
            },
            "manual_browser_evidence": [
                {"id": "attachment-compose", "status": "required", "observation": "Attach local .md/.markdown text before send."},
                {"id": "attachment-collapsed-lazy", "status": "required", "observation": "Body stays unfetched until expansion."},
                {"id": "attachment-safe-rendering", "status": "required", "observation": "Capture raw-HTML-disabled, safe-link, Mermaid, SVG, and CSP rendering evidence."},
            ],
            "cleanup": {"status": "not_needed", "table_id": None},
            "operations": {"active": None, "trace": []},
        }

    def _open(
        self,
        url: str,
        operation: str,
        *,
        token: str | None = None,
        method: str = "GET",
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[int, bytes, Mapping[str, str]]:
        request_headers = dict(headers or {})
        if token is not None:
            request_headers["Authorization"] = f"Bearer {token}"
        try:
            with urlopen(Request(url, data=body, headers=request_headers, method=method), timeout=30) as response:
                return response.status, response.read(), dict(response.headers.items())
        except HTTPError as error:
            return error.code, b"", dict(error.headers.items())
        except (OSError, URLError, TimeoutError) as error:
            raise VerificationError(f"HTTPS request failed during {operation}") from error

    def status(self, actual: int, expected: set[int], operation: str) -> None:
        if actual not in expected:
            raise VerificationError(f"{operation} returned HTTP {actual}; expected {sorted(expected)}")

    def json_request(
        self,
        url: str,
        operation: str,
        *,
        token: str | None = None,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
        expected: set[int] | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"} if payload is not None else None
        status, response, _headers = self.transport(url, operation, token=token, method=method, body=body, headers=headers)
        self.status(status, expected or {200}, operation)
        try:
            decoded = json.loads(response)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise VerificationError(f"{operation} did not return a JSON object") from error
        if not isinstance(decoded, dict):
            raise VerificationError(f"{operation} did not return a JSON object")
        return decoded

    def text_request(self, url: str, operation: str, *, token: str | None = None) -> str:
        status, body, _headers = self.transport(url, operation, token=token)
        self.status(status, {200}, operation)
        if len(body) > 8 * 1024 * 1024:
            raise VerificationError(f"{operation} response exceeded the verifier bound")
        try:
            return body.decode()
        except UnicodeDecodeError as error:
            raise VerificationError(f"{operation} response was not UTF-8") from error

    def mcp(self, method: str, params: Mapping[str, Any] | None, operation: str, *, notification: bool = False) -> dict[str, Any]:
        self.request_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notification:
            payload["id"] = self.request_id
        if params is not None:
            payload["params"] = params
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        status, body, response_headers = self.transport(
            f"{self.base_url}/mcp/", operation, token=self.admin, method="POST",
            body=json.dumps(payload, separators=(",", ":")).encode(), headers=headers,
        )
        self.status(status, {200, 202}, operation)
        self.session = response_headers.get("Mcp-Session-Id") or self.session
        if notification and not body:
            return {}
        try:
            text = body.decode().strip()
            event = next((line[5:].strip() for line in text.splitlines() if line.startswith("data:")), "")
            response = json.loads(text if text.startswith("{") else event)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise VerificationError(f"{operation} did not return MCP JSON") from error
        if not isinstance(response, dict):
            raise VerificationError(f"{operation} did not return an MCP object")
        return response

    def tool(self, name: str, arguments: Mapping[str, Any], *, expect_error: bool = False) -> dict[str, Any] | str:
        response = self.mcp("tools/call", {"name": name, "arguments": arguments}, f"MCP {name}")
        result = response.get("result")
        decoded = dict(result["structuredContent"]) if isinstance(result, Mapping) and isinstance(result.get("structuredContent"), Mapping) else None
        content = result.get("content") if isinstance(result, Mapping) else None
        if decoded is None and not isinstance(content, list):
            raise VerificationError(f"MCP {name} did not return content")
        for item in content or []:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                try:
                    candidate = json.loads(item["text"])
                except json.JSONDecodeError as error:
                    raise VerificationError(f"MCP {name} returned non-JSON content") from error
                if isinstance(candidate, dict):
                    decoded = candidate
                    break
        if decoded is None:
            raise VerificationError(f"MCP {name} did not return a JSON payload")
        error = decoded.get("error")
        code = str(error.get("code", "UNKNOWN_ERROR")).upper().replace("-", "_") if isinstance(error, Mapping) else None
        if expect_error:
            if code is None:
                raise VerificationError(f"MCP {name} unexpectedly succeeded")
            return code
        if decoded.get("ok") is False or code is not None:
            raise VerificationError(f"MCP {name} returned {code or 'UNKNOWN_ERROR'}")
        data = decoded.get("data", decoded)
        if not isinstance(data, Mapping):
            raise VerificationError(f"MCP {name} did not return an object")
        return dict(data)

    def metadata(self, items: object, operation: str) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            raise VerificationError(f"{operation} did not return attachment metadata")
        result: list[dict[str, Any]] = []
        for position, item in enumerate(items):
            required = {"id", "position", "name", "media_type", "byte_size"}
            if not isinstance(item, Mapping) or "content" in item or not required <= set(item):
                raise VerificationError(f"{operation} exposed malformed or complete attachment data")
            if item.get("position") != position or item.get("media_type") != "text/markdown":
                raise VerificationError(f"{operation} returned unordered attachment metadata")
            result.append(dict(item))
        return result

    def sayings(self, payload: Mapping[str, Any], operation: str) -> list[dict[str, Any]]:
        values = payload.get("sayings")
        if not isinstance(values, list):
            raise VerificationError(f"{operation} did not return sayings")
        result: list[dict[str, Any]] = []
        for saying in values:
            if not isinstance(saying, Mapping):
                raise VerificationError(f"{operation} returned a malformed saying")
            self.metadata(saying.get("attachments"), operation)
            result.append(dict(saying))
        return result

    def checkpoint(self, operation: str, action: Any, failure: VerificationError | str | None = None) -> Any:
        state = self.report["operations"]
        state["active"] = operation
        state["trace"].append({"operation": operation, "status": "started"})
        self.write()
        result = action()
        if failure is not None and not result:
            raise failure if isinstance(failure, VerificationError) else VerificationError(failure)
        state["trace"].append({"operation": operation, "status": "completed"})
        state["active"] = None
        self.write()
        return result

    def record_operation_failure(self) -> None:
        state = self.report["operations"]
        operation = state["active"]
        previous = next(
            (event["operation"] for event in reversed(state["trace"]) if event["status"] == "completed"),
            None,
        )
        if operation is not None:
            state["trace"].append({"operation": operation, "status": "failed", "previous_completed": previous})
            state["failed"], state["failed_after"] = state.get("failed", operation), state.get("failed_after", previous)
        state["active"] = None
        self.write()

    def sqlite_baseline(self, observed: Mapping[str, Any], operation: str) -> dict[str, Any]:
        state = observed.get("sqlite")
        names = ("tables", "sayings", "sayings_fts", "seats", "saying_attachments", "idempotency_keys")
        if not isinstance(state, Mapping):
            raise VerificationError(f"{operation} did not return a SQLite baseline")
        counts, table_ids = state.get("counts"), state.get("table_ids")
        if not isinstance(counts, Mapping) or set(counts) != set(names) or any(type(counts.get(name)) is not int or counts[name] < 0 for name in names) or not isinstance(table_ids, list) or any(not isinstance(table_id, str) for table_id in table_ids) or table_ids != sorted(table_ids) or counts["tables"] != len(table_ids) or type(state.get("device")) is not int or type(state.get("inode")) is not int or state.get("integrity_check") != "ok" or state.get("foreign_key_check") != 0:
            raise VerificationError(f"{operation} did not return a healthy SQLite baseline")
        return dict(state)

    def remote(self) -> dict[str, Any]:
        code = f"""
import hashlib, json, sqlite3, subprocess
from pathlib import Path
release = Path({RELEASE_DIR!r}); previous = Path({PREVIOUS_RELEASE_DIR!r}); wheel = Path({STAGED_WHEEL!r})
database = Path('/var/lib/tasca/tasca.db')
database_stat = database.stat()
connection = sqlite3.connect(database)
try:
    table_ids = sorted(str(row[0]) for row in connection.execute('SELECT id FROM tables'))
    counts = {{name: connection.execute(f'SELECT COUNT(*) FROM {{name}}').fetchone()[0] for name in ('tables', 'sayings', 'sayings_fts', 'seats', 'saying_attachments', 'idempotency_keys')}}
    counts['tables'] = len(table_ids)
    integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
    foreign_key_check = len(connection.execute('PRAGMA foreign_key_check').fetchall())
    attachments = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='saying_attachments'").fetchone() is not None
finally:
    connection.close()
service = subprocess.run(['systemctl', 'show', 'tasca.service', '--property=ExecStart', '--value'], capture_output=True, text=True).stdout
listeners = subprocess.run(['ss', '-ltnH', 'sport', '=', ':8000'], capture_output=True, text=True).stdout
print(json.dumps({{'service_execstart_matches': str(release / 'venv/bin/tasca') in service, 'release_dir_exists': release.is_dir(), 'release_python': subprocess.run([str(release / 'venv/bin/python'), '-c', 'import sys; print(f"{{sys.implementation.name}}:{{sys.version_info.major}}.{{sys.version_info.minor}}")'], capture_output=True, text=True).stdout.strip(), 'wheel_sha256': hashlib.sha256(wheel.read_bytes()).hexdigest() if wheel.is_file() else None, 'producer_sha256': hashlib.sha256(Path('/usr/local/lib/tasca/attachment-forward-deploy.sh').read_bytes()).hexdigest() if Path('/usr/local/lib/tasca/attachment-forward-deploy.sh').is_file() else None, 'sqlite_integrity': integrity, 'saying_attachments_table': attachments, 'sqlite': {{'device': database_stat.st_dev, 'inode': database_stat.st_ino, 'integrity_check': integrity, 'foreign_key_check': foreign_key_check, 'table_ids': table_ids, 'counts': counts}}, 'loopback_8000': bool(listeners.strip()) and all('127.0.0.1:' in line or '[::1]:' in line for line in listeners.splitlines()), 'rollback_0_1_31_ready': (previous / 'venv/bin/tasca').is_file(), 'caddy_active': subprocess.run(['systemctl', 'is-active', 'caddy'], capture_output=True, text=True).stdout.strip() == 'active'}}, sort_keys=True))
"""
        command = f"sudo {shlex.quote(REMOTE_PYTHON)} -c {shlex.quote(code)}"
        result = subprocess.run(["gcloud", "compute", "ssh", self.args.vm, "--project", self.args.project, "--zone", self.args.zone, "--quiet", "--command", command], capture_output=True, text=True, check=False)
        try:
            observed = json.loads(result.stdout) if result.returncode == 0 else None
        except json.JSONDecodeError:
            observed = None
        if not isinstance(observed, dict):
            raise VerificationError("VM read-only observation failed")
        required = {"service_execstart_matches": True, "release_dir_exists": True, "release_python": "cpython:3.13", "sqlite_integrity": "ok", "saying_attachments_table": True, "loopback_8000": True, "rollback_0_1_31_ready": True, "caddy_active": True}
        if any(observed.get(key) != value for key, value in required.items()) or not all(isinstance(observed.get(key), str) for key in ("wheel_sha256", "producer_sha256")):
            raise VerificationError("VM observation did not bind required release state")
        self.sqlite_baseline(observed, "VM observation")
        return observed

    def remote_fixture_json(self, code: str, operation: str) -> dict[str, Any]:
        command = f"sudo {shlex.quote(REMOTE_PYTHON)} -c {shlex.quote(code)}"
        result = subprocess.run(["gcloud", "compute", "ssh", self.args.vm, "--project", self.args.project, "--zone", self.args.zone, "--quiet", "--command", command], capture_output=True, text=True, check=False)
        try:
            observed = json.loads(result.stdout) if result.returncode == 0 else None
        except json.JSONDecodeError:
            observed = None
        if not isinstance(observed, dict):
            raise CleanupBlocked(f"{operation} could not be reconciled")
        return observed

    def reconcile_fixture(self) -> bool:
        if self.fixture_question is None or self.table_create_dedup_id is None:
            raise CleanupBlocked("fixture reconciliation identity is incomplete")
        code = f"""
import json, sqlite3
question = {self.fixture_question!r}; dedup_id = {self.table_create_dedup_id!r}
connection = sqlite3.connect('/var/lib/tasca/tasca.db')
try:
    table_ids = sorted(str(row[0]) for row in connection.execute('SELECT id FROM tables WHERE question = ?', (question,)))
    rows = connection.execute('SELECT response_data FROM idempotency_keys WHERE resource_key = ? AND tool_name = ? AND dedup_id = ?', ('table_create', 'table_create', dedup_id)).fetchall()
    response_ids = []; invalid_responses = 0
    for response_data, in rows:
        try:
            payload = json.loads(response_data); data = payload.get('data', payload) if isinstance(payload, dict) else None
            candidate = data.get('table_id') or data.get('id') if isinstance(data, dict) else None
        except (TypeError, ValueError):
            candidate = None
        if isinstance(candidate, str) and candidate:
            response_ids.append(candidate)
        else:
            invalid_responses += 1
finally:
    connection.close()
print(json.dumps({{'table_ids': table_ids, 'dedup_count': len(rows), 'dedup_table_ids': sorted(response_ids), 'invalid_dedup_responses': invalid_responses}}, sort_keys=True))
"""
        observed = self.remote_fixture_json(code, "fixture reconciliation")
        table_ids = observed.get("table_ids")
        response_ids = observed.get("dedup_table_ids")
        dedup_count = observed.get("dedup_count")
        invalid = observed.get("invalid_dedup_responses")
        if not isinstance(table_ids, list) or not isinstance(response_ids, list) or any(not isinstance(value, str) for value in [*table_ids, *response_ids]):
            raise CleanupBlocked("fixture reconciliation was malformed")
        if table_ids == [] and dedup_count == 0 and response_ids == [] and invalid == 0:
            self.table_create_idempotency_count = 0
            return False
        if len(table_ids) != 1 or dedup_count not in {0, 1} or invalid != 0:
            raise CleanupBlocked("fixture reconciliation was ambiguous")
        table_id = table_ids[0]
        if dedup_count == 1 and response_ids != [table_id] or dedup_count == 0 and response_ids:
            raise CleanupBlocked("fixture reconciliation was inconsistent")
        self.table_id = table_id
        self.table_create_idempotency_count = dedup_count
        return True

    def remote_cleanup(self) -> dict[str, Any]:
        if self.table_id is None or self.table_create_dedup_id is None or self.table_create_idempotency_count not in {0, 1}:
            raise CleanupBlocked("fixture identity is incomplete for idempotency cleanup")
        targets = [{"resource_key": "table_create", "tool_name": "table_create", "dedup_id": self.table_create_dedup_id, "expected_count": self.table_create_idempotency_count}]
        if self.table_say_dedup_id is not None:
            if self.table_say_idempotency_count != 1:
                raise CleanupBlocked("table_say idempotency state is unresolved")
            targets.append({"resource_key": f"saying:{self.table_id}:human", "tool_name": "table_say", "dedup_id": self.table_say_dedup_id, "expected_count": 1})
        code = f"""
import json, sqlite3
table_id = {self.table_id!r}; targets = json.loads({json.dumps(targets, sort_keys=True)!r})
connection = sqlite3.connect('/var/lib/tasca/tasca.db')
try:
    connection.execute('BEGIN IMMEDIATE')
    for target in targets:
        matches = connection.execute('SELECT 1 FROM idempotency_keys WHERE resource_key = ? AND tool_name = ? AND dedup_id = ?', (target['resource_key'], target['tool_name'], target['dedup_id'])).fetchall()
        if len(matches) != target['expected_count']:
            raise RuntimeError('fixture idempotency rows were not exact')
    test_domain_rows = {{'tables': connection.execute('SELECT COUNT(*) FROM tables WHERE id = ?', (table_id,)).fetchone()[0], 'sayings': connection.execute('SELECT COUNT(*) FROM sayings WHERE table_id = ?', (table_id,)).fetchone()[0], 'sayings_fts': connection.execute('SELECT COUNT(*) FROM sayings_fts WHERE rowid IN (SELECT rowid FROM sayings WHERE table_id = ?)', (table_id,)).fetchone()[0], 'seats': connection.execute('SELECT COUNT(*) FROM seats WHERE table_id = ?', (table_id,)).fetchone()[0], 'saying_attachments': connection.execute('SELECT COUNT(*) FROM saying_attachments WHERE saying_id IN (SELECT id FROM sayings WHERE table_id = ?)', (table_id,)).fetchone()[0]}}
    if any(test_domain_rows.values()):
        raise RuntimeError('fixture domain rows remain after batch deletion')
    deleted_rows = 0
    for target in targets:
        if target['expected_count']:
            deleted = connection.execute('DELETE FROM idempotency_keys WHERE resource_key = ? AND tool_name = ? AND dedup_id = ?', (target['resource_key'], target['tool_name'], target['dedup_id']))
            if deleted.rowcount != 1:
                raise RuntimeError('fixture idempotency delete was not exact')
            deleted_rows += 1
    connection.commit()
except BaseException:
    connection.rollback()
    raise
finally:
    connection.close()
print(json.dumps({{'idempotency_rows_deleted': deleted_rows, 'test_domain_rows': test_domain_rows}}, sort_keys=True))
"""
        observed = self.remote_fixture_json(code, "remote idempotency cleanup")
        test_domain_rows = observed.get("test_domain_rows")
        if not isinstance(test_domain_rows, Mapping) or set(test_domain_rows) != {"tables", "sayings", "sayings_fts", "seats", "saying_attachments"} or any(test_domain_rows.get(name) != 0 for name in test_domain_rows) or observed.get("idempotency_rows_deleted") != sum(target["expected_count"] for target in targets):
            raise CleanupBlocked("remote idempotency cleanup was not exact")
        return observed

    def cli_export(self, table_id: str, format_name: str) -> str:
        command = f"sudo -u tasca env TASCA_DB_PATH=/var/lib/tasca/tasca.db {shlex.quote(RELEASE_DIR + '/venv/bin/tasca')} export {shlex.quote(table_id)} --format {shlex.quote(format_name)}"
        result = subprocess.run(["gcloud", "compute", "ssh", self.args.vm, "--project", self.args.project, "--zone", self.args.zone, "--quiet", "--command", command], capture_output=True, text=True, check=False)
        if result.returncode != 0 or len(result.stdout.encode()) > 8 * 1024 * 1024:
            raise VerificationError("installed release CLI export failed")
        return result.stdout

    def export_signature(self, jsonl: str, markdown: str, names: Sequence[str]) -> tuple[str, str]:
        records = [json.loads(line) for line in jsonl.splitlines() if line]
        if len(records) < 3 or records[0].get("export_version") != "0.2" or records[0].get("table_id") != self.table_id:
            raise VerificationError("JSONL export did not bind format 0.2 and the test table")
        records[0].pop("exported_at", None)
        if markdown.find("## Transcript") < 0 or markdown.find("## Attachments") <= markdown.find("## Transcript") or any(name not in markdown for name in names):
            raise VerificationError("Markdown export did not append complete attachment material")
        return (
            hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            hashlib.sha256(markdown.encode()).hexdigest(),
        )

    def auth_and_inventory(self) -> str:
        health = self.json_request(f"{self.base_url}/api/v1/health", "public health")
        if health.get("version") != RELEASE_VERSION:
            raise VerificationError("public health did not report 0.1.32")
        for path in ("/api/v1/ready", "/docs", "/openapi.json"):
            self.text_request(f"{self.base_url}{path}", f"public {path}")
        shell = self.text_request(f"{self.base_url}/", "public SPA shell")
        asset = re.search(r'(?:src|href)="(/assets/[^"?#]+)', shell)
        if asset is None:
            raise VerificationError("public SPA shell did not reference a static asset")
        self.text_request(f"{self.base_url}{asset.group(1)}", "public SPA asset")
        anonymous, _body, _headers = self.transport(f"{self.base_url}/api/v1/auth/validate", "anonymous role")
        viewer_mode = "public" if anonymous == 200 else "configured" if anonymous == 401 else "invalid"
        if viewer_mode == "invalid" or viewer_mode == "configured" and self.viewer is None:
            raise VerificationError("Viewer authorization mode could not be verified")
        expected_read = {200} if viewer_mode == "public" else {401}
        status, _body, _headers = self.transport(f"{self.base_url}/api/v1/tables", "anonymous read")
        self.status(status, expected_read, "anonymous read")
        status, _body, _headers = self.transport(f"{self.base_url}/api/v1/tables", "anonymous create", method="POST", body=b"{}", headers={"Content-Type": "application/json"})
        self.status(status, {401, 403}, "anonymous create")
        if self.json_request(f"{self.base_url}/api/v1/auth/validate", "Admin role", token=self.admin).get("role") != "admin":
            raise VerificationError("runtime Admin credential did not validate as admin")
        if self.viewer:
            if self.json_request(f"{self.base_url}/api/v1/auth/validate", "Viewer role", token=self.viewer).get("role") != "viewer":
                raise VerificationError("runtime Viewer credential did not validate as viewer")
            status, _body, _headers = self.transport(f"{self.base_url}/api/v1/tables", "Viewer read", token=self.viewer)
            self.status(status, {200}, "Viewer read")
            status, _body, _headers = self.transport(f"{self.base_url}/api/v1/tables", "Viewer create", token=self.viewer, method="POST", body=b"{}", headers={"Content-Type": "application/json"})
            self.status(status, {401, 403}, "Viewer create")
        initialize = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {}}}).encode()
        for label, token in (("anonymous MCP", None), ("Viewer MCP", self.viewer)):
            if label == "Viewer MCP" and token is None:
                continue
            status, _body, _headers = self.transport(f"{self.base_url}/mcp/", label, token=token, method="POST", body=initialize, headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
            self.status(status, {401, 403}, label)
        initialized = self.mcp("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "tasca-attachments-verifier", "version": RELEASE_VERSION}}, "Admin MCP initialize")
        if not isinstance(initialized.get("result"), Mapping):
            raise VerificationError("Admin MCP initialize did not return a result")
        self.mcp("notifications/initialized", {}, "Admin MCP initialized", notification=True)
        listed = self.mcp("tools/list", {}, "Admin MCP tools/list").get("result")
        tools = listed.get("tools") if isinstance(listed, Mapping) else None
        names = [item.get("name") for item in tools if isinstance(item, Mapping) and isinstance(item.get("name"), str)] if isinstance(tools, list) else []
        if len(names) != 18 or len(set(names)) != 18 or set(names) != EXPECTED_TOOLS:
            raise VerificationError("MCP tools/list did not match the exact 18-tool attachment inventory")
        try:
            connection = socket.create_connection((self.args.host, 8000), timeout=5)
        except OSError:
            pass
        else:
            connection.close()
            raise VerificationError("public TCP/8000 accepted a connection")
        self.report["auth"] = {"viewer_mode": viewer_mode, "public": True, "admin": True}
        self.report["mcp_inventory"] = {"count": 18, "exact": True}
        return viewer_mode

    def attachments(self, viewer_mode: str) -> dict[str, Any]:
        marker = uuid.uuid4().hex
        needle = f"attachment-only-{marker}"
        self.fixture_question = f"Tasca attachment verifier {marker}"
        self.table_create_dedup_id = f"verify-table-{marker}"
        self.table_create_attempted = True
        created = self.json_request(f"{self.base_url}/api/v1/tables", "REST test table create", token=self.admin, method="POST", payload={"title": self.fixture_question, "dedup_id": self.table_create_dedup_id}, expected={200, 201})
        self.table_id = str(created.get("table_id") or created.get("id") or "")
        if not self.table_id:
            raise VerificationError("REST test table create did not return an ID")
        self.table_create_idempotency_count = 1
        self.report["cleanup"] = {"status": "pending", "table_id": self.table_id, "table_create_dedup_id": self.table_create_dedup_id, "table_say_dedup_id": self.table_say_dedup_id}
        rest = self.json_request(f"{self.base_url}/api/v1/tables/{self.table_id}/sayings", "REST attachment create", token=self.admin, method="POST", payload={"speaker_name": "attachment-verifier", "content": "REST attachment verification body", "attachments": [{"name": "rest-one.md", "content": f"# REST\n{needle}"}, {"name": "rest-two.markdown", "content": "## REST two"}]}, expected={201})
        rest_id = str(rest.get("id") or rest.get("saying_id") or "")
        rest_attachments = self.metadata(rest.get("attachments"), "REST attachment create")
        if not rest_id or len(rest_attachments) != 2:
            raise VerificationError("REST attachment create did not return ordered metadata")
        read_token = self.viewer if viewer_mode == "configured" else self.admin
        listed = self.json_request(f"{self.base_url}/api/v1/tables/{self.table_id}/sayings", "REST list", token=read_token)
        before_count = len(self.sayings(listed, "REST list"))
        joined = self.json_request(f"{self.base_url}/api/v1/tables/join", "REST join", token=read_token, method="POST", payload={"table_id": self.table_id})
        if not isinstance(joined.get("initial"), Mapping):
            raise VerificationError("REST join did not return initial history")
        self.sayings(joined["initial"], "REST join")
        waited = self.json_request(f"{self.base_url}/api/v1/tables/{self.table_id}/sayings/wait?since_sequence=-1&timeout=0", "REST wait", token=read_token)
        self.sayings(waited, "REST wait")
        body = self.json_request(f"{self.base_url}/api/v1/tables/{self.table_id}/sayings/{rest_id}/attachments/{rest_attachments[0]['id']}", "REST attachment read", token=read_token)
        if body.get("id") != rest_attachments[0]["id"] or not isinstance(body.get("content"), str):
            raise VerificationError("REST attachment read did not return the selected body")
        self.table_say_dedup_id = f"verify-say-{marker}"
        say = {"table_id": self.table_id, "content": "MCP attachment verification body", "speaker_kind": "human", "speaker_name": "attachment-verifier", "dedup_id": self.table_say_dedup_id, "attachments": [{"name": "mcp-one.md", "content": f"# MCP\n@{needle}"}, {"name": "mcp-two.md", "content": "## MCP two"}]}
        mcp_say = self.checkpoint("mcp.table_say.first", lambda: self.tool("table_say", say))
        assert isinstance(mcp_say, dict)
        self.table_say_idempotency_count = 1
        retry = self.checkpoint("mcp.table_say.retry", lambda: self.tool("table_say", say))
        assert isinstance(retry, dict)
        mcp_attachments, retry_attachments = self.checkpoint("mcp.table_say.retry_metadata", lambda: (self.metadata(mcp_say.get("attachments"), "MCP table_say"), self.metadata(retry.get("attachments"), "MCP table_say retry")))
        self.checkpoint("mcp.table_say.retry_identity", lambda: mcp_say.get("id") == retry.get("id") and mcp_say.get("sequence") == retry.get("sequence") and [item["id"] for item in mcp_attachments] == [item["id"] for item in retry_attachments], "MCP retry did not retain saying and attachment identity")
        self.checkpoint("mcp.table_say.attachment_mentions", lambda: mcp_say.get("mentions_all") is False and mcp_say.get("mentions_resolved") == [] and mcp_say.get("mentions_unresolved") == [], "attachment-only text affected mention resolution")
        full = self.checkpoint("mcp.attachment_get", lambda: self.tool("attachment_get", {"attachment_ids": [item["id"] for item in mcp_attachments]}))
        assert isinstance(full, dict)
        bodies = full.get("attachments")
        self.checkpoint("mcp.attachment_get.validation", lambda: isinstance(bodies, list) and [item.get("id") for item in bodies if isinstance(item, Mapping)] == [item["id"] for item in mcp_attachments] and all(isinstance(item, Mapping) and isinstance(item.get("content"), str) for item in bodies), "MCP attachment_get did not return ordered full bodies")
        invalid = {"name": [{"name": "bad.txt", "content": "x"}], "count": [{"name": f"{index}.md", "content": "x"} for index in range(9)], "per_item": [{"name": "large.md", "content": "x" * (256 * 1024 + 1)}], "aggregate": [{"name": f"large-{index}.md", "content": "x" * (256 * 1024)} for index in range(4)] + [{"name": "overflow.md", "content": "x"}]}
        for name, values in invalid.items():
            self.checkpoint(f"mcp.table_say.invalid.{name}", lambda values=values: self.tool("table_say", {"table_id": self.table_id, "content": "invalid attachment admission", "speaker_kind": "human", "speaker_name": "attachment-verifier", "attachments": values}, expect_error=True) in {"INVALID_REQUEST", "LIMIT_EXCEEDED"}, f"MCP invalid {name} returned an unexpected error")
        after = self.checkpoint("mcp.table_say.post_invalid", lambda: self.tool("table_say", {"table_id": self.table_id, "content": "post-invalid sequence verification", "speaker_kind": "human", "speaker_name": "attachment-verifier"}))
        assert isinstance(after, dict)
        self.checkpoint("mcp.table_say.post_invalid_sequence", lambda: isinstance(mcp_say.get("sequence"), int) and after.get("sequence") == mcp_say["sequence"] + 1, "invalid attachments consumed a saying sequence")
        self.checkpoint("mcp.table_listen", lambda: self.sayings(self.tool("table_listen", {"table_id": self.table_id, "since_sequence": -1, "limit": 50}), "MCP listen"))
        self.checkpoint("mcp.table_wait", lambda: self.sayings(self.tool("table_wait", {"table_id": self.table_id, "since_sequence": -1, "wait_ms": 1, "limit": 50}), "MCP wait"))
        final_sayings = self.checkpoint("rest.post_invalid_sayings", lambda: self.sayings(self.json_request(f"{self.base_url}/api/v1/tables/{self.table_id}/sayings", "REST invalid readback", token=read_token), "REST invalid readback"))
        self.checkpoint("mcp.table_say.post_invalid_count", lambda: len(final_sayings) == before_count + 2, "invalid attachment admission changed the saying count")
        self.checkpoint("rest.search.attachment_only", lambda: isinstance((hits := self.json_request(f"{self.base_url}/api/v1/search?{urlencode({'q': needle})}", "attachment-only search", token=read_token).get("hits")), list) and not any(isinstance(hit, Mapping) and (hit.get("table_id") == self.table_id or hit.get("id") == self.table_id) for hit in hits), "attachment-only content entered saying search")
        http_jsonl = self.checkpoint("rest.export.jsonl", lambda: self.text_request(f"{self.base_url}/api/v1/tables/{self.table_id}/export/jsonl", "HTTP JSONL export", token=read_token))
        http_markdown = self.checkpoint("rest.export.markdown", lambda: self.text_request(f"{self.base_url}/api/v1/tables/{self.table_id}/export/markdown", "HTTP Markdown export", token=read_token))
        mcp_jsonl = self.checkpoint("mcp.export.jsonl", lambda: self.tool("table_export", {"table_id": self.table_id, "format": "jsonl"}))
        mcp_markdown = self.checkpoint("mcp.export.markdown", lambda: self.tool("table_export", {"table_id": self.table_id, "format": "markdown"}))
        assert isinstance(mcp_jsonl, dict) and isinstance(mcp_markdown, dict)
        cli_jsonl = self.checkpoint("cli.export.jsonl", lambda: self.cli_export(self.table_id, "jsonl"))
        cli_markdown = self.checkpoint("cli.export.markdown", lambda: self.cli_export(self.table_id, "md"))
        values = (http_jsonl, mcp_jsonl.get("content"), cli_jsonl), (http_markdown, mcp_markdown.get("content"), cli_markdown)
        names = [item["name"] for item in [*rest_attachments, *mcp_attachments]]
        self.checkpoint("exports.content", lambda: all(isinstance(item, str) for group in values for item in group), "MCP export did not return content")
        signatures = self.checkpoint("exports.signature_comparison", lambda: {self.export_signature(jsonl, markdown, names) for jsonl, markdown in zip(*values, strict=True)})
        self.checkpoint("exports.signature_agreement", lambda: len(signatures) == 1, "HTTP, MCP, and installed CLI exports disagreed")
        return {"table_id": self.table_id, "rest_attachment_ids": [item["id"] for item in rest_attachments], "mcp_attachment_ids": [item["id"] for item in mcp_attachments], "invalid_admission": "rejected_without_sequence_consumption", "search_and_mentions": "attachment_only_content_excluded", "exports": {"jsonl_0_2": next(iter(signatures))[0], "markdown": next(iter(signatures))[1]}}

    def binding(self) -> dict[str, str]:
        root = Path(__file__).parents[2]
        paths = {
            "forward_producer": root / "scripts/gcp/attachment-forward-deploy.sh",
            "verifier": Path(__file__),
        }
        revisions: dict[str, str] = {}
        for label, path in paths.items():
            revision = subprocess.run(
                ["git", "-C", str(root), "log", "-1", "--format=%H", "--", str(path.relative_to(root))],
                capture_output=True,
                text=True,
                check=False,
            )
            if revision.returncode != 0 or not revision.stdout.strip():
                raise VerificationError(f"{label} revision could not be resolved")
            revisions[f"{label}_commit"] = revision.stdout.strip()
        return {
            **revisions,
            "forward_producer_sha256": hashlib.sha256(paths["forward_producer"].read_bytes()).hexdigest(),
            "verifier_sha256": hashlib.sha256(paths["verifier"].read_bytes()).hexdigest(),
        }

    def ensure_cleanup_session(self) -> None:
        """Initialize an Admin MCP session when failure preceded normal session setup."""
        if self.session is not None:
            return
        initialized = self.mcp(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "tasca-attachments-cleanup", "version": RELEASE_VERSION},
            },
            "Admin MCP cleanup initialize",
        )
        if not isinstance(initialized.get("result"), Mapping):
            raise CleanupBlocked("Admin MCP cleanup initialize did not return a result")
        self.mcp("notifications/initialized", {}, "Admin MCP cleanup initialized", notification=True)
        if self.session is None:
            raise CleanupBlocked("Admin MCP cleanup did not establish a session")

    def cleanup(self) -> None:
        if self.table_id is None:
            if not self.table_create_attempted:
                return
            if not self.checkpoint("cleanup.fixture_reconciliation", self.reconcile_fixture):
                self.report["cleanup"] = {"status": "not_needed", "table_id": None, "reconciliation": "no_effect"}
                return
        before = self.report.get("runtime_before")
        if not isinstance(before, Mapping):
            raise CleanupBlocked("pre-test SQLite baseline is unavailable")
        try:
            baseline = self.sqlite_baseline(before, "pre-test SQLite baseline")
        except VerificationError as error:
            raise CleanupBlocked("pre-test SQLite baseline is unhealthy") from error
        self.checkpoint("cleanup.mcp.session", self.ensure_cleanup_session)
        closed = self.checkpoint("cleanup.mcp.close", lambda: self.tool("table_control", {"table_id": self.table_id, "action": "close", "speaker_name": "tasca-attachments-cleanup", "reason": "bounded verifier cleanup"}))
        self.checkpoint("cleanup.mcp.close_validation", lambda: isinstance(closed, Mapping) and closed.get("table_status") == "closed", "MCP cleanup close did not close the fixture")
        deleted = self.checkpoint("cleanup.mcp.batch_delete", lambda: self.tool("table_delete_batch", {"ids": [self.table_id]}))
        self.checkpoint("cleanup.mcp.batch_delete_validation", lambda: isinstance(deleted, Mapping) and deleted.get("deleted_count") == 1 and deleted.get("failed") == [] and deleted.get("deleted_ids") in (None, [self.table_id]), "MCP cleanup batch delete did not delete exactly the fixture")
        self.checkpoint("cleanup.mcp.not_found", lambda: self.tool("table_get", {"table_id": self.table_id}, expect_error=True) == "NOT_FOUND", "MCP cleanup did not prove fixture absence")
        idempotency = self.checkpoint("cleanup.idempotency", self.remote_cleanup)
        after = self.checkpoint("cleanup.remote_baseline", self.remote)
        try:
            restored = self.sqlite_baseline(after, "post-cleanup SQLite baseline")
        except VerificationError as error:
            raise CleanupBlocked("post-cleanup SQLite baseline is unhealthy") from error
        self.checkpoint("cleanup.baseline_reconciliation", lambda: restored == baseline, CleanupBlocked("test-data cleanup did not restore the exact SQLite baseline"))
        self.report["runtime_after"] = after
        self.report["cleanup"] = {"status": "verified", "table_id": self.table_id, "table_create_dedup_id": self.table_create_dedup_id, "table_say_dedup_id": self.table_say_dedup_id, "absence": "MCP_NOT_FOUND", "idempotency": idempotency, "baseline_restored": True}

    def write(self) -> Path:
        path = Path(self.args.report_dir) / "attachment-live-evidence.json"
        encoded = json.dumps(self.report, sort_keys=True, indent=2) + "\n"
        if len(encoded.encode()) > MAX_REPORT_BYTES or self.admin in encoded or self.viewer and self.viewer in encoded:
            raise VerificationError("verification report was oversized or credential-bearing")
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, stat.S_IRWXU)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(encoded)
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, path)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return path

    def run(self) -> Path:
        failure: BaseException | None = None
        try:
            bound = self.binding()
            before = self.remote()
            if before["producer_sha256"] != bound["forward_producer_sha256"]:
                raise VerificationError("VM forward producer did not match the local committed producer")
            self.report["binding"] = {**bound, "deployed_wheel_sha256": before["wheel_sha256"]}
            self.report["runtime_before"] = before
            self.report["attachments"] = self.attachments(self.auth_and_inventory())
            self.report["status"] = "MACHINE_CHECKS_PASS"
        except BaseException as error:
            failure = error
            if isinstance(error, VerificationError):
                self.record_operation_failure()
            self.report["failure"] = {"kind": type(error).__name__}
        finally:
            if self.table_id is not None or self.table_create_attempted:
                try:
                    self.cleanup()
                except BaseException as cleanup_error:
                    if isinstance(cleanup_error, VerificationError):
                        self.record_operation_failure()
                    self.report["status"] = "MACHINE_CHECKS_BLOCKED"
                    self.report["cleanup"] = {
                        "status": "failed",
                        "table_id": self.table_id,
                        "table_create_dedup_id": self.table_create_dedup_id,
                        "table_say_dedup_id": self.table_say_dedup_id,
                        "failure": type(cleanup_error).__name__,
                    }
                    failure = CleanupBlocked("test-data cleanup could not be reconciled")
            path = self.write()
        if failure is not None:
            raise failure
        return path


# @invar:allow shell_result: The standalone producer converts its secret-safe report path into one POSIX exit status.
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--zone", required=True)
    parser.add_argument("--vm", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--report-dir", required=True)
    try:
        report = AttachmentVerifier(parser.parse_args()).run()
    except Exception as error:
        print(f"attachment remote verification failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "MACHINE_CHECKS_PASS", "evidence": str(report)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
