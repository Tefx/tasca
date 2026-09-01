#!/usr/bin/env python3
"""Verify the deployed Viewer/Admin HTTPS release without exposing credentials.

The verifier has two required phases. ``--prepare`` creates and reads a table
through Admin MCP, then writes a 0600 token-free receipt. The rollout-owned
``reapply`` action marks that receipt only after the service has restarted.
``--cleanup`` requires that mark, reads the exact table again, deletes it, and
proves its absence.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

RELEASE_VERSION = "0.1.30"
PROJECT_ID = "rda-engineering"
ZONE = "asia-southeast1-b"
VM = "tasca-mcp"
STATE_FORMAT = "tasca-viewer-auth-persistence-v1"
STATE_MAX_BYTES = 4096
_PUBLIC_PATHS = ("/api/v1/health", "/api/v1/ready", "/docs", "/openapi.json", "/")
_SECRET_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


# @invar:allow shell_result: The standalone verifier raises malformed endpoint errors to its redacted CLI boundary.
# @shell_complexity: HTTPS validation rejects each credential-unsafe URL component explicitly.
def require_https_base_url(url: str) -> str:
    """Return a certificate-valid HTTPS origin without credentials or a path.

    >>> require_https_base_url("https://tasca.example")
    'https://tasca.example'
    >>> require_https_base_url("http://tasca.example")
    Traceback (most recent call last):
    ...
    ValueError: verifier requires an HTTPS base URL
    """
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise ValueError("verifier requires an HTTPS base URL")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("verifier URL must not include credentials")
    if parsed.port not in (None, 443):
        raise ValueError("verifier requires standard HTTPS port 443")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("verifier base URL must not include a path, query, or fragment")
    return urlunsplit(("https", parsed.netloc, "", "", ""))


# @invar:allow shell_result: Process-boundary secret access exposes values only to in-memory callers.
# @shell_complexity: Secret name, project, subprocess status, and empty-value checks must fail independently.
def load_secret(project: str, secret_name: str) -> str:
    """Read one named Secret Manager value without placing it in argv or diagnostics."""
    if project != PROJECT_ID:
        raise ValueError("verifier project must be rda-engineering")
    if not _SECRET_NAME.fullmatch(secret_name):
        raise ValueError("Secret Manager secret name is invalid")
    result = subprocess.run(
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            "latest",
            "--project",
            project,
            "--secret",
            secret_name,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Secret Manager access failed")
    value = result.stdout.rstrip("\n")
    if not value:
        raise RuntimeError("Secret Manager returned an empty credential")
    return value


# @invar:allow shell_result: Direct-port probe treats only a failed public TCP connection as success.
def require_direct_port_refusal(address: str) -> None:
    """Require that the supplied public TCP/8000 endpoint cannot be connected to."""
    host, separator, raw_port = address.rpartition(":")
    if not separator or not host or not raw_port.isdecimal() or int(raw_port) != 8000:
        raise ValueError("direct port must be HOST:8000")
    try:
        connection = socket.create_connection((host, 8000), timeout=5)
    except OSError:
        return
    connection.close()
    raise RuntimeError("public TCP/8000 accepted a connection")


# @invar:allow shell_result: HTTP adapter discards remote error bodies and raises only to the redacted CLI boundary.
def _open(request: Request, operation: str) -> tuple[int, bytes, Mapping[str, str]]:
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, response.read(), response.headers
    except HTTPError as error:
        return error.code, b"", error.headers
    except URLError as error:
        raise RuntimeError(f"request failed during {operation}") from error


# @invar:allow shell_result: HTTP adapter carries a credential only in an Authorization header.
def request(
    url: str,
    operation: str,
    *,
    token: str | None = None,
    method: str = "GET",
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, bytes, Mapping[str, str]]:
    """Perform one HTTPS request without retaining remote error text."""
    request_headers = dict(headers or {})
    if token is not None:
        request_headers["Authorization"] = f"Bearer {token}"
    return _open(Request(url, data=body, headers=request_headers, method=method), operation)


def require_status(status: int, allowed: set[int], operation: str) -> None:
    if status not in allowed:
        expected = ", ".join(str(code) for code in sorted(allowed))
        raise RuntimeError(f"{operation} returned HTTP {status}; expected {expected}")


# @invar:allow shell_result: JSON decoder raises only protocol-shape errors to the redacted CLI boundary.
def json_object(body: bytes, operation: str) -> dict[str, Any]:
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{operation} did not return JSON") from error
    if not isinstance(decoded, dict):
        raise RuntimeError(f"{operation} returned a non-object JSON value")
    return decoded


# @invar:allow shell_result: REST adapter raises only protocol-shape errors to the redacted CLI boundary.
def request_json(
    url: str,
    operation: str,
    *,
    token: str | None = None,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    allowed: set[int] | None = None,
) -> dict[str, Any]:
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else None
    status, response_body, _headers = request(
        url, operation, token=token, method=method, body=body, headers=headers
    )
    require_status(status, allowed or {200}, operation)
    return json_object(response_body, operation)


# @invar:allow shell_result: MCP response decoder raises only protocol-shape errors to the redacted CLI boundary.
# @shell_complexity: Decoder distinguishes JSON, SSE, missing payload, and non-object response failures.
def parse_mcp_response(body: str) -> dict[str, Any]:
    """Decode a JSON response or first JSON server-sent-event payload."""
    stripped = body.strip()
    if stripped.startswith("{"):
        decoded = json.loads(stripped)
    else:
        decoded = None
        for line in stripped.splitlines():
            if line.startswith("data:"):
                decoded = json.loads(line.removeprefix("data:").strip())
                break
        if decoded is None:
            raise ValueError("MCP response did not contain JSON")
    if not isinstance(decoded, dict):
        raise ValueError("MCP response was not an object")
    return decoded


# @invar:allow shell_result: MCP transport raises only protocol-shape errors to the redacted CLI boundary.
# @shell_complexity: JSON-RPC construction distinguishes notifications, request IDs, parameters, and session headers.
def invoke(
    mcp_url: str,
    token: str | None,
    method: str,
    params: Mapping[str, Any] | None,
    request_id: int | None,
    session_id: str | None,
    operation: str,
) -> tuple[dict[str, Any], str | None]:
    """Send one JSON-RPC request and reject error/malformed envelopes."""
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        payload["id"] = request_id
    if params is not None:
        payload["params"] = params
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
    status, body, response_headers = request(
        mcp_url,
        operation,
        token=token,
        method="POST",
        body=json.dumps(payload).encode(),
        headers=headers,
    )
    require_status(status, {200, 202}, operation)
    next_session = response_headers.get("Mcp-Session-Id") or session_id
    if request_id is None and not body.strip():
        return {}, next_session
    response = parse_mcp_response(body.decode())
    if "error" in response:
        raise RuntimeError(f"{operation} returned an MCP error envelope")
    if request_id is not None and "result" not in response:
        raise RuntimeError(f"{operation} omitted the MCP result envelope")
    return response, next_session


# @invar:allow shell_result: Public workflow returns a receipt or raises to the redacted CLI boundary.
def verify_public_surfaces(base_url: str, expected_version: str) -> dict[str, str]:
    """Verify TLS-backed health/docs/SPA paths and one referenced static asset."""
    responses: dict[str, bytes] = {}
    for path in _PUBLIC_PATHS:
        status, body, _headers = request(f"{base_url}{path}", f"public {path}")
        require_status(status, {200}, f"public {path}")
        responses[path] = body
    health = json_object(responses["/api/v1/health"], "public health")
    if health.get("version") != expected_version:
        raise RuntimeError("public health did not report the expected release version")
    shell = responses["/"].decode(errors="replace")
    asset = re.search(r'(?:src|href)="(/assets/[^"?#]+)"', shell)
    if asset is None:
        raise RuntimeError("public SPA shell did not reference a static asset")
    status, _body, _headers = request(f"{base_url}{asset.group(1)}", "public static asset")
    require_status(status, {200}, "public static asset")
    return {"health": "public", "ready": "public", "docs": "public", "spa": "public"}


# @invar:allow shell_result: Role validation adapts a single authenticated response to its role contract.
def validate_role(base_url: str, token: str, expected_role: str) -> None:
    result = request_json(
        f"{base_url}/api/v1/auth/validate", "credential validation", token=token
    )
    if result.get("role") != expected_role:
        raise RuntimeError(f"credential validation did not return {expected_role} role")


# @invar:allow shell_result: REST workflow returns only after each authorization branch has been exercised.
# @shell_complexity: Public/configured Viewer modes require distinct read expectations and Viewer credential handling.
def verify_rest_matrix(
    base_url: str, viewer_mode: str, admin_token: str, viewer_token: str | None
) -> None:
    """Exercise unauthenticated, Viewer, and Admin REST read/mutation behavior."""
    expected_unauthenticated_read = {200} if viewer_mode == "public" else {401}
    status, _body, _headers = request(
        f"{base_url}/api/v1/tables", "unauthenticated REST read"
    )
    require_status(status, expected_unauthenticated_read, "unauthenticated REST read")
    status, _body, _headers = request(
        f"{base_url}/api/v1/tables",
        "unauthenticated REST mutation",
        method="POST",
        body=b"{}",
        headers={"Content-Type": "application/json"},
    )
    require_status(status, {401, 403}, "unauthenticated REST mutation")
    if viewer_mode == "configured":
        if viewer_token is None:
            raise RuntimeError("configured Viewer verification needs a Viewer credential")
        status, _body, _headers = request(
            f"{base_url}/api/v1/tables", "Viewer REST read", token=viewer_token
        )
        require_status(status, {200}, "Viewer REST read")
        status, _body, _headers = request(
            f"{base_url}/api/v1/tables",
            "Viewer REST mutation",
            token=viewer_token,
            method="POST",
            body=b"{}",
            headers={"Content-Type": "application/json"},
        )
        require_status(status, {401, 403}, "Viewer REST mutation")
        validate_role(base_url, viewer_token, "viewer")
    validate_role(base_url, admin_token, "admin")
    status, _body, _headers = request(
        f"{base_url}/api/v1/tables", "Admin REST read", token=admin_token
    )
    require_status(status, {200}, "Admin REST read")


# @invar:allow shell_result: Tool content parser rejects any response that does not carry a table identifier.
# @shell_complexity: MCP tool content has separately validated result, content, text, JSON, data, and ID layers.
def mcp_table_id(response: Mapping[str, Any], operation: str) -> str:
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise RuntimeError(f"{operation} did not return an MCP result object")
    content = result.get("content")
    if not isinstance(content, list) or not content or not isinstance(content[0], Mapping):
        raise RuntimeError(f"{operation} did not return MCP text content")
    text = content[0].get("text")
    if not isinstance(text, str):
        raise RuntimeError(f"{operation} did not return MCP text")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{operation} returned non-JSON MCP content") from error
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"{operation} returned a non-object MCP payload")
    data = payload.get("data", payload)
    if not isinstance(data, Mapping):
        raise RuntimeError(f"{operation} did not return MCP table data")
    table_id = data.get("id") or data.get("table_id")
    if not isinstance(table_id, str) or not table_id:
        raise RuntimeError(f"{operation} did not return a table ID")
    return table_id


# @invar:allow shell_result: MCP workflow returns a token-free table ID after complete denial/tool/create/get checks.
# @shell_complexity: It enforces unauthenticated/Viewer denials, session initialization, tool-set completeness, and exact create/get identity.
def verify_mcp_prepare(
    mcp_url: str, admin_token: str, viewer_token: str | None, viewer_mode: str
) -> str:
    """Require both tools and create/read one exact table through Admin MCP."""
    initialize_params = {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "tasca-viewer-auth-verifier", "version": RELEASE_VERSION},
    }
    for label, token in (("unauthenticated MCP", None), ("Viewer MCP", viewer_token)):
        if label == "Viewer MCP" and viewer_mode == "public":
            continue
        status, _body, _headers = request(
            mcp_url,
            label,
            token=token,
            method="POST",
            body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": initialize_params}).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        )
        require_status(status, {401, 403}, label)
    initialized, session_id = invoke(
        mcp_url, admin_token, "initialize", initialize_params, 2, None, "Admin MCP initialize"
    )
    if not isinstance(initialized.get("result"), Mapping):
        raise RuntimeError("Admin MCP initialize did not return a result object")
    invoke(
        mcp_url,
        admin_token,
        "notifications/initialized",
        {},
        None,
        session_id,
        "Admin MCP initialized notification",
    )
    tools, session_id = invoke(
        mcp_url, admin_token, "tools/list", {}, 3, session_id, "Admin MCP tools/list"
    )
    result = tools.get("result")
    tool_list = result.get("tools") if isinstance(result, Mapping) else None
    if not isinstance(tool_list, list):
        raise RuntimeError("Admin MCP tools/list did not return a tools list")
    names = {tool.get("name") for tool in tool_list if isinstance(tool, Mapping)}
    required_tools = {"table_create", "table_get"}
    if not required_tools.issubset(names):
        raise RuntimeError("Admin MCP tools/list omitted required table_create or table_get")
    created, session_id = invoke(
        mcp_url,
        admin_token,
        "tools/call",
        {
            "name": "table_create",
            "arguments": {
                "question": "GCP Viewer authentication persistence verification",
                "context": "Created and removed by the tracked deployment verifier.",
                "dedup_id": f"gcp-viewer-auth-verify-{uuid.uuid4()}",
            },
        },
        4,
        session_id,
        "Admin MCP table_create",
    )
    table_id = mcp_table_id(created, "Admin MCP table_create")
    readback, _session_id = invoke(
        mcp_url,
        admin_token,
        "tools/call",
        {"name": "table_get", "arguments": {"table_id": table_id}},
        5,
        session_id,
        "Admin MCP table_get",
    )
    if mcp_table_id(readback, "Admin MCP table_get") != table_id:
        raise RuntimeError("Admin MCP table_get returned a different table ID")
    return table_id


# @invar:allow shell_result: Persistence workflow rejects an incomplete state receipt or a non-exact MCP readback.
# @shell_complexity: Reapply verification requires session setup, tool-set validation, and exact persisted ID readback.
def verify_mcp_persistence_readback(mcp_url: str, admin_token: str, table_id: str) -> None:
    """Initialize Admin MCP and require exact `table_get` identity after reapply."""
    initialize_params = {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "tasca-viewer-auth-verifier", "version": RELEASE_VERSION},
    }
    initialized, session_id = invoke(
        mcp_url, admin_token, "initialize", initialize_params, 6, None, "persistence MCP initialize"
    )
    if not isinstance(initialized.get("result"), Mapping):
        raise RuntimeError("persistence MCP initialize did not return a result object")
    invoke(
        mcp_url,
        admin_token,
        "notifications/initialized",
        {},
        None,
        session_id,
        "persistence MCP initialized notification",
    )
    tools, session_id = invoke(
        mcp_url, admin_token, "tools/list", {}, 7, session_id, "persistence MCP tools/list"
    )
    result = tools.get("result")
    tool_list = result.get("tools") if isinstance(result, Mapping) else None
    names = {tool.get("name") for tool in tool_list if isinstance(tool, Mapping)} if isinstance(tool_list, list) else set()
    if not {"table_create", "table_get"}.issubset(names):
        raise RuntimeError("persistence MCP tools/list omitted required table_create or table_get")
    readback, _session_id = invoke(
        mcp_url,
        admin_token,
        "tools/call",
        {"name": "table_get", "arguments": {"table_id": table_id}},
        8,
        session_id,
        "post-reapply Admin MCP table_get",
    )
    if mcp_table_id(readback, "post-reapply Admin MCP table_get") != table_id:
        raise RuntimeError("post-reapply Admin MCP table_get returned a different table ID")


# @invar:allow shell_result: Cleanup workflow validates deletion and absence at the HTTPS API boundary.
def cleanup_table(base_url: str, admin_token: str, table_id: str) -> None:
    status, _body, _headers = request(
        f"{base_url}/api/v1/tables/{table_id}",
        "Admin REST cleanup",
        token=admin_token,
        method="DELETE",
    )
    require_status(status, {200, 204}, "Admin REST cleanup")
    status, _body, _headers = request(
        f"{base_url}/api/v1/tables/{table_id}",
        "Admin REST cleanup absence",
        token=admin_token,
    )
    require_status(status, {404}, "Admin REST cleanup absence")


# @invar:allow shell_result: Default state-path resolution is a CLI configuration adapter, not a domain operation.
def default_state_file() -> Path:
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    return Path(os.environ.get("TASCA_VIEWER_AUTH_STATE_FILE", runtime / "tasca-viewer-auth-state.json"))


# @invar:allow shell_result: State writer creates a bounded token-free interoperability receipt for rollout reapply.
def write_prepared_state(path: Path, base_url: str, expected_version: str, table_id: str) -> None:
    """Persist the token-free prepare receipt with exact 0600 permissions."""
    state = {
        "format": STATE_FORMAT,
        "phase": "prepared",
        "base_url": base_url,
        "expected_version": expected_version,
        "table_id": table_id,
    }
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n"
    if len(encoded.encode()) > STATE_MAX_BYTES:
        raise RuntimeError("verification state exceeds its bounded size")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(encoded)
    os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(temporary, path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


# @invar:allow shell_result: State loader requires the rollout-owned reapply mark before cleanup can read/delete data.
# @shell_complexity: Bounded state validation distinguishes file, mode, JSON, schema, phase, and ID failures.
def load_reapplied_state(path: Path, base_url: str, expected_version: str) -> str:
    """Load the bounded 0600 reapply receipt and return its prepared table ID."""
    if not path.is_file() or path.stat().st_size > STATE_MAX_BYTES:
        raise RuntimeError("persistence verification state is missing or oversized")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RuntimeError("persistence verification state must have mode 0600")
    try:
        state = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise RuntimeError("persistence verification state is not JSON") from error
    expected_keys = {"format", "phase", "base_url", "expected_version", "table_id"}
    if not isinstance(state, dict) or set(state) != expected_keys:
        raise RuntimeError("persistence verification state has an invalid schema")
    if (
        state.get("format") != STATE_FORMAT
        or state.get("phase") != "reapplied"
        or state.get("base_url") != base_url
        or state.get("expected_version") != expected_version
    ):
        raise RuntimeError("persistence verification state was not marked by the matching rollout reapply")
    table_id = state.get("table_id")
    if not isinstance(table_id, str) or not table_id or len(table_id) > 255:
        raise RuntimeError("persistence verification state has an invalid table ID")
    return table_id


# @invar:allow shell_result: Service-log reader captures evidence without printing it or accepting credential values as arguments.
def fetch_service_logs(project: str) -> str:
    """Fetch bounded systemd evidence through the fixed deployment target."""
    result = subprocess.run(
        [
            "gcloud",
            "compute",
            "ssh",
            VM,
            "--project",
            project,
            "--zone",
            ZONE,
            "--quiet",
            "--command",
            "sudo journalctl -u tasca.service -n 500 --no-pager",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("service-log evidence fetch failed")
    return result.stdout


# @invar:allow shell_result: Evidence scanner refuses any retained output or service log containing either configured credential.
def require_redacted_evidence(
    receipt: Mapping[str, Any], service_logs: str, tokens: tuple[str | None, ...]
) -> None:
    """Reject credential leakage without returning the evidence or credential values."""
    retained_output = json.dumps(receipt, sort_keys=True)
    if any(token and (token in retained_output or token in service_logs) for token in tokens):
        raise RuntimeError("credential appeared in retained verifier or service-log evidence")


# @invar:allow shell_result: Credential selector returns process-local values only to the verifier workflow.
def access_tokens(
    project: str, viewer_secret: str, admin_secret: str, viewer_mode: str
) -> tuple[str, str | None]:
    admin_token = load_secret(project, admin_secret)
    if viewer_mode == "public":
        return admin_token, None
    viewer_token = load_secret(project, viewer_secret)
    if secrets.compare_digest(admin_token, viewer_token):
        raise RuntimeError("configured Viewer and Admin credentials must differ")
    return admin_token, viewer_token


# @invar:allow shell_result: Prepare orchestrates public transport, configured authorization matrices, and a token-free state receipt.
# @shell_complexity: Prepare must sequence TLS/public checks, direct-port refusal, secret access, REST/MCP matrices, state persistence, and evidence scan.
def prepare(args: argparse.Namespace, base_url: str) -> dict[str, Any]:
    public = verify_public_surfaces(base_url, args.expected_version)
    require_direct_port_refusal(args.check_direct_port)
    admin_token, viewer_token = access_tokens(
        args.project, args.viewer_secret, args.admin_secret, args.viewer_mode
    )
    verify_rest_matrix(base_url, args.viewer_mode, admin_token, viewer_token)
    table_id = verify_mcp_prepare(
        f"{base_url}/mcp/", admin_token, viewer_token, args.viewer_mode
    )
    state_file = default_state_file()
    write_prepared_state(state_file, base_url, args.expected_version, table_id)
    receipt: dict[str, Any] = {
        "phase": "prepared",
        "public": public,
        "rest_matrix": f"{args.viewer_mode}-verified",
        "mcp": "create-and-exact-get-verified",
        "persistence_state": str(state_file),
        "table_id": table_id,
    }
    require_redacted_evidence(receipt, fetch_service_logs(args.project), (admin_token, viewer_token))
    return receipt


# @invar:allow shell_result: Cleanup requires rollout reapply, exact MCP persistence readback, deletion, absence, and evidence scan.
# @shell_complexity: Cleanup sequences public checks, direct-port refusal, secret access, state validation, exact MCP readback, deletion, and evidence scan.
def cleanup(args: argparse.Namespace, base_url: str) -> dict[str, Any]:
    public = verify_public_surfaces(base_url, args.expected_version)
    require_direct_port_refusal(args.check_direct_port)
    admin_token, viewer_token = access_tokens(
        args.project, args.viewer_secret, args.admin_secret, args.viewer_mode
    )
    table_id = load_reapplied_state(default_state_file(), base_url, args.expected_version)
    verify_rest_matrix(base_url, args.viewer_mode, admin_token, viewer_token)
    verify_mcp_persistence_readback(f"{base_url}/mcp/", admin_token, table_id)
    cleanup_table(base_url, admin_token, table_id)
    receipt: dict[str, Any] = {
        "phase": "cleanup",
        "public": public,
        "rest_matrix": f"{args.viewer_mode}-verified",
        "mcp": "post-reapply-exact-get-verified",
        "cleanup": "verified",
        "table_id": table_id,
    }
    require_redacted_evidence(receipt, fetch_service_logs(args.project), (admin_token, viewer_token))
    default_state_file().unlink()
    return receipt


# @invar:allow shell_result: CLI entry point returns a POSIX exit code after redacted verification.
# @shell_complexity: Argument validation enforces an exact downstream command contract and mutually exclusive two-phase modes.
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--viewer-secret", required=True)
    parser.add_argument("--admin-secret", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--check-direct-port", required=True)
    parser.add_argument("--viewer-mode", choices=("public", "configured"), default="configured")
    phase = parser.add_mutually_exclusive_group(required=True)
    phase.add_argument("--prepare", action="store_true")
    phase.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()

    if args.project != PROJECT_ID:
        raise RuntimeError("verifier project must be rda-engineering")
    if args.expected_version != RELEASE_VERSION:
        raise RuntimeError("verifier expected version must be 0.1.30")
    raw_base_url = os.environ.get("TASCA_HTTPS_BASE_URL")
    if not raw_base_url:
        raise RuntimeError("TASCA_HTTPS_BASE_URL is required")
    base_url = require_https_base_url(raw_base_url)
    receipt = prepare(args, base_url) if args.prepare else cleanup(args, base_url)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Viewer authentication remote verification failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
