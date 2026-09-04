#!/usr/bin/env python3
"""Produce a secret-safe live acceptance report for the Tasca 0.1.32 attachment release.

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
REPORT_FORMAT = "verifier.report.v2"
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
    """A verifier error that deliberately omits remote response text."""


class CleanupBlocked(VerificationError):
    """Created test data could not be reconciled safely."""


class AttachmentVerifier:
    """Stateful, direct HTTPS/MCP verifier for one target and one temporary table."""

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
        self.report: dict[str, Any] = {
            "format": REPORT_FORMAT,
            "status": "FAIL",
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

    def remote(self, test_table_id: str | None = None) -> dict[str, Any]:
        code = f"""
import hashlib, json, sqlite3, subprocess
from pathlib import Path
release = Path({RELEASE_DIR!r}); previous = Path({PREVIOUS_RELEASE_DIR!r}); wheel = Path({STAGED_WHEEL!r})
connection = sqlite3.connect('/var/lib/tasca/tasca.db')
test_id = {test_table_id!r}
test_rows = None if test_id is None else connection.execute("SELECT (SELECT COUNT(*) FROM tables WHERE id = ?) + (SELECT COUNT(*) FROM sayings WHERE table_id = ?) + (SELECT COUNT(*) FROM saying_attachments WHERE saying_id IN (SELECT id FROM sayings WHERE table_id = ?))", (test_id, test_id, test_id)).fetchone()[0]
integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
attachments = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='saying_attachments'").fetchone() is not None
count = connection.execute('SELECT COUNT(*) FROM tables').fetchone()[0]; connection.close()
service = subprocess.run(['systemctl', 'show', 'tasca.service', '--property=ExecStart', '--value'], capture_output=True, text=True).stdout
listeners = subprocess.run(['ss', '-ltnH', 'sport', '=', ':8000'], capture_output=True, text=True).stdout
print(json.dumps({{'service_execstart_matches': str(release / 'venv/bin/tasca') in service, 'release_dir_exists': release.is_dir(), 'release_python': subprocess.run([str(release / 'venv/bin/python'), '-c', 'import sys; print(f"{{sys.implementation.name}}:{{sys.version_info.major}}.{{sys.version_info.minor}}")'], capture_output=True, text=True).stdout.strip(), 'wheel_sha256': hashlib.sha256(wheel.read_bytes()).hexdigest() if wheel.is_file() else None, 'producer_sha256': hashlib.sha256(Path('/usr/local/lib/tasca/attachment-forward-deploy.sh').read_bytes()).hexdigest() if Path('/usr/local/lib/tasca/attachment-forward-deploy.sh').is_file() else None, 'sqlite_integrity': integrity, 'saying_attachments_table': attachments, 'domain_table_count': count, 'test_data_rows': test_rows, 'loopback_8000': bool(listeners.strip()) and all('127.0.0.1:' in line or '[::1]:' in line for line in listeners.splitlines()), 'rollback_0_1_31_ready': (previous / 'venv/bin/tasca').is_file(), 'caddy_active': subprocess.run(['systemctl', 'is-active', 'caddy'], capture_output=True, text=True).stdout.strip() == 'active'}}, sort_keys=True))
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
        created = self.json_request(f"{self.base_url}/api/v1/tables", "REST test table create", token=self.admin, method="POST", payload={"title": f"Tasca attachment verifier {marker}", "dedup_id": f"verify-table-{marker}"}, expected={200, 201})
        self.table_id = str(created.get("table_id") or created.get("id") or "")
        if not self.table_id:
            raise VerificationError("REST test table create did not return an ID")
        self.report["cleanup"] = {"status": "pending", "table_id": self.table_id}
        rest = self.json_request(f"{self.base_url}/api/v1/tables/{self.table_id}/sayings", "REST attachment create", token=self.admin, method="POST", payload={"speaker_name": "attachment-verifier", "content": "REST attachment verification body", "attachments": [{"name": "rest-one.md", "content": f"# REST\n{needle}"}, {"name": "rest-two.markdown", "content": "## REST two"}]}, expected={201})
        rest_id = str(rest.get("id") or rest.get("saying_id") or "")
        rest_attachments = self.metadata(rest.get("attachments"), "REST attachment create")
        if not rest_id or len(rest_attachments) != 2:
            raise VerificationError("REST attachment create did not return ordered metadata")
        listed = self.json_request(f"{self.base_url}/api/v1/tables/{self.table_id}/sayings", "REST list", token=self.admin)
        before_count = len(self.sayings(listed, "REST list"))
        joined = self.json_request(f"{self.base_url}/api/v1/tables/join", "REST join", method="POST", payload={"table_id": self.table_id})
        if not isinstance(joined.get("initial"), Mapping):
            raise VerificationError("REST join did not return initial history")
        self.sayings(joined["initial"], "REST join")
        waited = self.json_request(f"{self.base_url}/api/v1/tables/{self.table_id}/sayings/wait?since_sequence=-1&timeout=0", "REST wait", token=self.admin)
        self.sayings(waited, "REST wait")
        read_token = self.viewer if viewer_mode == "configured" else self.admin
        body = self.json_request(f"{self.base_url}/api/v1/tables/{self.table_id}/sayings/{rest_id}/attachments/{rest_attachments[0]['id']}", "REST attachment read", token=read_token)
        if body.get("id") != rest_attachments[0]["id"] or not isinstance(body.get("content"), str):
            raise VerificationError("REST attachment read did not return the selected body")
        say = {"table_id": self.table_id, "content": "MCP attachment verification body", "speaker_kind": "human", "speaker_name": "attachment-verifier", "dedup_id": f"verify-say-{marker}", "attachments": [{"name": "mcp-one.md", "content": f"# MCP\n@{needle}"}, {"name": "mcp-two.md", "content": "## MCP two"}]}
        mcp_say = self.tool("table_say", say)
        retry = self.tool("table_say", say)
        assert isinstance(mcp_say, dict) and isinstance(retry, dict)
        mcp_attachments = self.metadata(mcp_say.get("attachments"), "MCP table_say")
        if mcp_say.get("id") != retry.get("id") or [item["id"] for item in mcp_attachments] != [item["id"] for item in self.metadata(retry.get("attachments"), "MCP retry")]:
            raise VerificationError("MCP retry did not retain saying and attachment IDs")
        if mcp_say.get("mentions_all") is not False or mcp_say.get("mentions_resolved") != [] or mcp_say.get("mentions_unresolved") != []:
            raise VerificationError("attachment-only text affected mention resolution")
        full = self.tool("attachment_get", {"attachment_ids": [item["id"] for item in mcp_attachments]})
        assert isinstance(full, dict)
        bodies = full.get("attachments")
        if not isinstance(bodies, list) or [item.get("id") for item in bodies if isinstance(item, Mapping)] != [item["id"] for item in mcp_attachments] or not all(isinstance(item, Mapping) and isinstance(item.get("content"), str) for item in bodies):
            raise VerificationError("MCP attachment_get did not return ordered full bodies")
        for name, values in {"name": [{"name": "bad.txt", "content": "x"}], "count": [{"name": f"{index}.md", "content": "x"} for index in range(9)], "item": [{"name": "large.md", "content": "x" * (256 * 1024 + 1)}], "total": [{"name": f"large-{index}.md", "content": "x" * (256 * 1024)} for index in range(4)] + [{"name": "overflow.md", "content": "x"}]}.items():
            code = self.tool("table_say", {"table_id": self.table_id, "content": "invalid attachment admission", "speaker_kind": "human", "speaker_name": "attachment-verifier", "attachments": values}, expect_error=True)
            if code not in {"INVALID_REQUEST", "LIMIT_EXCEEDED"}:
                raise VerificationError(f"MCP invalid {name} returned an unexpected error")
        after = self.tool("table_say", {"table_id": self.table_id, "content": "post-invalid sequence verification", "speaker_kind": "human", "speaker_name": "attachment-verifier"})
        assert isinstance(after, dict)
        if not isinstance(mcp_say.get("sequence"), int) or after.get("sequence") != mcp_say["sequence"] + 1:
            raise VerificationError("invalid attachments consumed a saying sequence")
        self.sayings(self.tool("table_listen", {"table_id": self.table_id, "since_sequence": -1, "limit": 50}), "MCP listen")
        self.sayings(self.tool("table_wait", {"table_id": self.table_id, "since_sequence": -1, "wait_ms": 1, "limit": 50}), "MCP wait")
        final_sayings = self.sayings(self.json_request(f"{self.base_url}/api/v1/tables/{self.table_id}/sayings", "REST invalid readback", token=self.admin), "REST invalid readback")
        if len(final_sayings) != before_count + 2:
            raise VerificationError("invalid attachment admission changed the saying count")
        hits = self.json_request(f"{self.base_url}/api/v1/search?{urlencode({'q': needle})}", "attachment-only search", token=self.admin).get("hits")
        if not isinstance(hits, list) or any(isinstance(hit, Mapping) and (hit.get("table_id") == self.table_id or hit.get("id") == self.table_id) for hit in hits):
            raise VerificationError("attachment-only content entered saying search")
        http_jsonl = self.text_request(f"{self.base_url}/api/v1/tables/{self.table_id}/export/jsonl", "HTTP JSONL export", token=self.admin)
        http_markdown = self.text_request(f"{self.base_url}/api/v1/tables/{self.table_id}/export/markdown", "HTTP Markdown export", token=self.admin)
        mcp_jsonl = self.tool("table_export", {"table_id": self.table_id, "format": "jsonl"})
        mcp_markdown = self.tool("table_export", {"table_id": self.table_id, "format": "markdown"})
        assert isinstance(mcp_jsonl, dict) and isinstance(mcp_markdown, dict)
        values = (http_jsonl, mcp_jsonl.get("content"), self.cli_export(self.table_id, "jsonl")), (http_markdown, mcp_markdown.get("content"), self.cli_export(self.table_id, "md"))
        names = [item["name"] for item in [*rest_attachments, *mcp_attachments]]
        if not all(isinstance(item, str) for group in values for item in group):
            raise VerificationError("MCP export did not return content")
        signatures = {self.export_signature(jsonl, markdown, names) for jsonl, markdown in zip(*values, strict=True)}
        if len(signatures) != 1:
            raise VerificationError("HTTP, MCP, and installed CLI exports disagreed")
        return {"table_id": self.table_id, "rest_attachment_ids": [item["id"] for item in rest_attachments], "mcp_attachment_ids": [item["id"] for item in mcp_attachments], "invalid_admission": "rejected_without_sequence_consumption", "search_and_mentions": "attachment_only_content_excluded", "exports": {"jsonl_0_2": next(iter(signatures))[0], "markdown": next(iter(signatures))[1]}}

    def binding(self) -> dict[str, str]:
        root = Path(__file__).parents[2]
        revision = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "HEAD"], capture_output=True, text=True, check=False)
        if revision.returncode != 0:
            raise VerificationError("local producer revision could not be resolved")
        return {"commit": revision.stdout.strip(), "forward_producer_sha256": hashlib.sha256((root / "scripts/gcp/attachment-forward-deploy.sh").read_bytes()).hexdigest(), "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}

    def cleanup(self) -> None:
        if self.table_id is None:
            return
        status, _body, _headers = self.transport(f"{self.base_url}/api/v1/tables/{self.table_id}", "test table cleanup", token=self.admin, method="DELETE")
        self.status(status, {200, 204}, "test table cleanup")
        status, _body, _headers = self.transport(f"{self.base_url}/api/v1/tables/{self.table_id}", "test table cleanup absence", token=self.admin)
        self.status(status, {404}, "test table cleanup absence")
        after = self.remote(self.table_id)
        if after.get("test_data_rows") != 0 or after.get("domain_table_count") != self.report["runtime_before"]["domain_table_count"]:
            raise CleanupBlocked("test-data cleanup did not preserve prior SQLite state")
        self.report["runtime_after"] = after
        self.report["cleanup"] = {"status": "verified", "table_id": self.table_id, "absence": "API_404_and_SQLite_0"}

    def write(self) -> Path:
        path = Path(self.args.report_dir) / "verifier.report.json"
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
            self.report["status"] = "PASS"
        except BaseException as error:
            failure = error
            self.report["failure"] = {"kind": type(error).__name__}
        finally:
            if self.table_id is not None:
                try:
                    self.cleanup()
                except BaseException as cleanup_error:
                    self.report["status"] = "BLOCKED"
                    self.report["cleanup"] = {"status": "failed", "table_id": self.table_id, "failure": type(cleanup_error).__name__}
                    failure = CleanupBlocked("test-data cleanup could not be reconciled")
            path = self.write()
        if failure is not None:
            raise failure
        return path


# @invar:allow shell_result: The standalone producer converts its secret-safe report path into one POSIX exit status.
def main() -> int:
    """Run exactly the target-bound verifier contract supplied by the live step."""
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
    print(json.dumps({"status": "PASS", "report": str(report)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
