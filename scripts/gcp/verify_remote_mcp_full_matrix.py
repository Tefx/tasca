#!/usr/bin/env python3
"""Verify the public HTTPS MCP surface with one isolated table lifecycle.

The verifier deliberately speaks Streamable HTTP directly instead of using a
local MCP client or proxy.  It performs all read-only and authorization gates
before creating a table, then exercises the exact 17-tool public inventory.  A
material failure after creation stops the matrix and runs only the bounded
close, exact batch-delete, and post-delete ``NOT_FOUND`` reconciliation.

The report is a token-free JSON receipt.  Credentials are accepted only from a
runtime environment variable and are never included in request URLs, request
bodies, diagnostics, or report data.  Unit tests inject the transport seam;
normal execution uses the standard-library HTTPS transport below.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

RELEASE_VERSION = "0.1.31"
MCP_PATH = "/mcp/"
DEFAULT_WAIT_MS = 1000
MAX_WAIT_MS = 10_000
REPORT_FORMAT = "tasca-remote-mcp-full-matrix-v1"
DEFAULT_CREDENTIAL_ENV = "TASCA_ADMIN_TOKEN"

# This order mirrors TOOL_CONTRACTS in src/tasca/shell/mcp/tool_contracts.py.
EXPECTED_TOOLS: tuple[str, ...] = (
    "patron_register",
    "patron_get",
    "table_create",
    "table_join",
    "table_get",
    "table_list",
    "table_delete_batch",
    "table_export",
    "table_say",
    "table_listen",
    "table_control",
    "table_update",
    "table_wait",
    "seat_heartbeat",
    "seat_list",
    "connect",
    "connection_status",
)
EXPECTED_TOOL_NAMES = frozenset(EXPECTED_TOOLS)
_SECRET_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

Transport = Callable[..., tuple[int, bytes, Mapping[str, str]]]


class VerificationError(RuntimeError):
    """A redacted verifier failure identified by operation and public code."""

    def __init__(
        self,
        message: str,
        *,
        operation: str = "verification",
        code: str | None = None,
    ) -> None:
        self.operation = operation
        self.code = code
        # ``message`` is supplied by this module only; retaining it makes unit
        # failures useful without retaining remote response bodies.
        super().__init__(message)


class BlockedVerification(VerificationError):
    """A failure whose cleanup or state truth remains unknown."""


class PatronReference:
    """An existing patron selected from the read-only reference table."""

    __slots__ = ("patron_id", "display_name")

    def __init__(self, patron_id: str, display_name: str) -> None:
        self.patron_id = patron_id
        self.display_name = display_name


class ToolMatrix:
    """Accumulate one report row per advertised tool without raw responses."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {
            name: {"tool": name, "status": "NOT_RUN", "evidence": []}
            for name in EXPECTED_TOOLS
        }

    def pass_tool(self, tool: str, evidence: Mapping[str, Any] | str) -> None:
        row = self.rows[tool]
        row["status"] = "PASS"
        row["evidence"].append(_safe_evidence(evidence))

    def fail_tool(self, tool: str, evidence: Mapping[str, Any] | str) -> None:
        row = self.rows[tool]
        row["status"] = "FAIL"
        row["evidence"].append(_safe_evidence(evidence))

    def pending_after_failure(self, operation: str) -> None:
        for _name, row in self.rows.items():
            if row["status"] == "NOT_RUN":
                row["evidence"].append(
                    {"stopped_after": operation, "reason": "fail_fast"}
                )

    def as_rows(self) -> list[dict[str, Any]]:
        return [self.rows[name] for name in EXPECTED_TOOLS]


# @invar:allow shell_result: Endpoint validation raises only redacted configuration errors.
def require_https_base_url(url: str) -> str:
    """Return a credential-free HTTPS origin suitable for the MCP endpoint.

    >>> require_https_base_url("https://tasca.example")
    'https://tasca.example'
    >>> require_https_base_url("http://tasca.example")
    Traceback (most recent call last):
    ...
    ValueError: verifier requires an HTTPS base URL
    """
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("verifier URL has an invalid port") from error
    if parsed.scheme.lower() != "https":
        raise ValueError("verifier requires an HTTPS base URL")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("verifier URL must not include credentials")
    if port not in (None, 443):
        raise ValueError("verifier requires standard HTTPS port 443")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("verifier base URL must not include a path, query, or fragment")
    return urlunsplit(("https", parsed.netloc, "", "", ""))


# @invar:allow shell_result: Runtime credential access exposes a value only to in-memory callers.
def load_runtime_credential(environment_name: str = DEFAULT_CREDENTIAL_ENV) -> str:
    """Read one existing credential from the process environment."""
    if not _SECRET_ENV_NAME.fullmatch(environment_name):
        raise ValueError("credential environment variable name is invalid")
    value = os.environ.get(environment_name)
    if value is None or not value:
        raise RuntimeError("configured runtime credential is missing")
    return value


# @invar:allow shell_result: HTTPS adapter discards remote error bodies before crossing the redacted boundary.
def _open(
    request: Request, operation: str
) -> tuple[int, bytes, Mapping[str, str]]:
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, response.read(), dict(response.headers.items())
    except HTTPError as error:
        # Error bodies can contain credentials or request details.  Do not
        # retain or report them; callers inspect only the status code.
        return error.code, b"", dict(error.headers.items())
    except (OSError, URLError, TimeoutError) as error:
        raise VerificationError(
            f"HTTPS request failed during {operation}", operation=operation
        ) from error


# @invar:allow shell_result: HTTPS requests carry credentials only in Authorization headers.
def request(
    url: str,
    operation: str,
    *,
    token: str | None = None,
    method: str = "GET",
    body: bytes | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, bytes, Mapping[str, str]]:
    """Perform one HTTPS request without retaining the credential-bearing request."""
    request_headers = dict(headers or {})
    if token is not None:
        request_headers["Authorization"] = f"Bearer {token}"
    return _open(Request(url, data=body, headers=request_headers, method=method), operation)


def require_status(status: int, allowed: set[int], operation: str) -> None:
    """Require an HTTP status while omitting response body details."""
    if status not in allowed:
        expected = ", ".join(str(value) for value in sorted(allowed))
        raise VerificationError(
            f"{operation} returned HTTP {status}; expected {expected}",
            operation=operation,
        )


def json_object(body: bytes, operation: str) -> dict[str, Any]:
    """Decode one JSON object without exposing malformed response content."""
    try:
        decoded = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise VerificationError(
            f"{operation} did not return JSON", operation=operation
        ) from error
    if not isinstance(decoded, dict):
        raise VerificationError(
            f"{operation} returned a non-object JSON value", operation=operation
        )
    return decoded


def request_json(
    url: str,
    operation: str,
    *,
    token: str | None = None,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    allowed: set[int] | None = None,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Issue an HTTP JSON request through the injectable transport seam."""
    body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else None
    send = transport or request
    status, response_body, _headers = send(
        url,
        operation,
        token=token,
        method=method,
        body=body,
        headers=headers,
    )
    require_status(status, allowed or {200}, operation)
    return json_object(response_body, operation)


# @invar:allow shell_result: MCP decoder returns only protocol-shaped objects and no response text.
def parse_mcp_response(body: str | bytes) -> dict[str, Any]:
    """Decode JSON or the first JSON ``data:`` event from Streamable HTTP."""
    if isinstance(body, bytes):
        try:
            text = body.decode()
        except UnicodeDecodeError as error:
            raise ValueError("MCP response was not UTF-8") from error
    else:
        text = body
    stripped = text.lstrip("\ufeff").strip()
    if not stripped:
        raise ValueError("MCP response did not contain JSON")

    decoded: Any = None
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError as error:
            raise ValueError("MCP response contained invalid JSON") from error
    else:
        for line in stripped.splitlines():
            if not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if not data or data == "[DONE]":
                continue
            try:
                decoded = json.loads(data)
            except json.JSONDecodeError as error:
                raise ValueError("MCP event contained invalid JSON") from error
            break

    if decoded is None:
        raise ValueError("MCP response did not contain JSON")
    if not isinstance(decoded, dict):
        raise ValueError("MCP response was not an object")
    return decoded


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Read a response header case-insensitively, including simple test fakes."""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


# @invar:allow shell_result: JSON-RPC adapter rejects only status/protocol errors at the redacted boundary.
def invoke(
    mcp_url: str,
    token: str | None,
    method: str,
    params: Mapping[str, Any] | None,
    request_id: int | None,
    session_id: str | None,
    operation: str,
    transport: Transport | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Send one Streamable HTTP JSON-RPC request and return its safe envelope."""
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        payload["id"] = request_id
    if params is not None:
        payload["params"] = params
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
    send = transport or request
    status, body, response_headers = send(
        mcp_url,
        operation,
        token=token,
        method="POST",
        body=json.dumps(payload, separators=(",", ":")).encode(),
        headers=headers,
    )
    require_status(status, {200, 202}, operation)
    next_session = _header(response_headers, "Mcp-Session-Id") or session_id
    if request_id is None and not body.strip():
        return {}, next_session
    try:
        response = parse_mcp_response(body)
    except ValueError as error:
        raise VerificationError(str(error), operation=operation) from error
    if request_id is not None and "result" not in response and "error" not in response:
        raise VerificationError(
            f"{operation} omitted the JSON-RPC result envelope", operation=operation
        )
    return response, next_session


class MCPClient:
    """Small direct Streamable HTTP client with an injectable request function."""

    def __init__(self, base_url: str, token: str, transport: Transport | None = None) -> None:
        self.mcp_url = f"{base_url}{MCP_PATH}"
        self.token = token
        self.transport = transport
        self.session_id: str | None = None
        self._request_id = 0
        # Set by the lifecycle immediately after a create response yields an
        # ID so a later response-shape failure can still be reconciled.
        self.active_table_id: str | None = None
        self.active_table_status: str | None = None

    def next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def rpc(
        self,
        method: str,
        params: Mapping[str, Any] | None,
        *,
        operation: str,
        notification: bool = False,
    ) -> dict[str, Any]:
        """Issue an authenticated request and preserve only the session ID."""
        request_id = None if notification else self.next_id()
        response, self.session_id = invoke(
            self.mcp_url,
            self.token,
            method,
            params,
            request_id,
            self.session_id,
            operation,
            self.transport,
        )
        return response

    def raw_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Call a tool and return its safe JSON payload, including expected errors."""
        response = self.rpc(
            "tools/call",
            {"name": name, "arguments": dict(arguments)},
            operation=f"MCP {name}",
        )
        return parse_tool_payload(response, f"MCP {name}")


def parse_tool_payload(response: Mapping[str, Any], operation: str) -> dict[str, Any]:
    """Extract the JSON payload carried in an MCP tool result."""
    if "error" in response:
        error = response.get("error")
        code = _error_code(error)
        raise VerificationError(
            f"{operation} returned JSON-RPC error {code}",
            operation=operation,
            code=code,
        )
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise VerificationError(
            f"{operation} did not return an MCP result object", operation=operation
        )
    structured = result.get("structuredContent")
    if isinstance(structured, Mapping):
        return dict(structured)
    content = result.get("content")
    if not isinstance(content, list) or not content:
        raise VerificationError(
            f"{operation} returned no MCP content", operation=operation
        )
    for item in content:
        if not isinstance(item, Mapping) or not isinstance(item.get("text"), str):
            continue
        try:
            decoded = json.loads(item["text"])
        except json.JSONDecodeError as error:
            raise VerificationError(
                f"{operation} returned non-JSON tool content", operation=operation
            ) from error
        if not isinstance(decoded, dict):
            raise VerificationError(
                f"{operation} returned a non-object tool payload", operation=operation
            )
        return decoded
    raise VerificationError(
        f"{operation} returned no JSON tool content", operation=operation
    )


def _error_code(error: object) -> str:
    """Normalize public error-code aliases without retaining remote messages."""
    if isinstance(error, Mapping):
        raw = error.get("code", "UNKNOWN_ERROR")
    else:
        raw = "UNKNOWN_ERROR"
    code = str(raw).upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "TABLENOTFOUND": "NOT_FOUND",
        "TABLE_NOT_FOUND": "NOT_FOUND",
        "INVALIDSTATE": "INVALID_STATE",
        "OPERATIONNOTALLOWED": "OPERATION_NOT_ALLOWED",
        "PERMISSIONDENIED": "PERMISSION_DENIED",
    }
    return aliases.get(code, code)


def tool_error_code(payload: Mapping[str, Any]) -> str | None:
    """Return a normalized tool error code, or ``None`` for success payloads."""
    error = payload.get("error")
    return _error_code(error) if isinstance(error, Mapping) else None


def require_tool_ok(payload: Mapping[str, Any], operation: str) -> dict[str, Any]:
    """Return tool data and reject a public error envelope without its message."""
    code = tool_error_code(payload)
    if payload.get("ok") is False or code is not None:
        raise VerificationError(
            f"{operation} returned MCP error {code or 'UNKNOWN_ERROR'}",
            operation=operation,
            code=code or "UNKNOWN_ERROR",
        )
    if payload.get("ok") is True:
        data = payload.get("data")
    else:
        # A few MCP-compatible servers return the data object directly.
        data = payload.get("data", payload)
    if not isinstance(data, Mapping):
        raise VerificationError(
            f"{operation} did not return an MCP data object", operation=operation
        )
    return dict(data)


def expect_tool_error(
    client: MCPClient,
    name: str,
    arguments: Mapping[str, Any],
    expected: set[str],
) -> str:
    """Call one tool and require one of the supplied public error codes."""
    payload = client.raw_tool(name, arguments)
    code = tool_error_code(payload)
    if code not in expected:
        actual = code or "SUCCESS"
        expected_text = ", ".join(sorted(expected))
        raise VerificationError(
            f"MCP {name} returned {actual}; expected {expected_text}",
            operation=f"MCP {name}",
            code=actual,
        )
    return code


def call_tool(
    client: MCPClient,
    name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    """Call a tool and return its result data."""
    payload = client.raw_tool(name, arguments)
    return require_tool_ok(payload, f"MCP {name}")


def _result_object(response: Mapping[str, Any], operation: str) -> Mapping[str, Any]:
    result = response.get("result")
    if not isinstance(result, Mapping):
        raise VerificationError(
            f"{operation} did not return an MCP result object", operation=operation
        )
    return result


def _extract_id(data: Mapping[str, Any], operation: str, keys: Sequence[str]) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    raise VerificationError(f"{operation} did not return an identifier", operation=operation)


def extract_table_id(data: Mapping[str, Any], operation: str) -> str:
    """Extract a table ID from direct, nested, or compatibility MCP shapes."""
    table = data.get("table")
    if isinstance(table, Mapping):
        return _extract_id(table, operation, ("table_id", "id"))
    return _extract_id(data, operation, ("table_id", "id"))


def extract_patron(data: Mapping[str, Any], operation: str) -> PatronReference:
    """Extract an existing patron from spec or compatibility response fields."""
    patron = data.get("patron")
    source = patron if isinstance(patron, Mapping) else data
    patron_id = _extract_id(source, operation, ("patron_id", "id"))
    name = source.get("display_name") or source.get("name")
    if not isinstance(name, str) or not name:
        raise VerificationError(f"{operation} omitted display_name", operation=operation)
    return PatronReference(patron_id=patron_id, display_name=name)


def extract_seats(data: Mapping[str, Any], operation: str) -> list[Mapping[str, Any]]:
    seats = data.get("seats")
    if not isinstance(seats, list) or not all(isinstance(seat, Mapping) for seat in seats):
        raise VerificationError(f"{operation} did not return a seats list", operation=operation)
    return [seat for seat in seats if isinstance(seat, Mapping)]


def extract_sayings(data: Mapping[str, Any], operation: str) -> list[Mapping[str, Any]]:
    sayings = data.get("sayings")
    if not isinstance(sayings, list) or not all(isinstance(item, Mapping) for item in sayings):
        raise VerificationError(
            f"{operation} did not return a sayings list", operation=operation
        )
    return [item for item in sayings if isinstance(item, Mapping)]


def _safe_evidence(value: Mapping[str, Any] | str) -> Any:
    """Keep report evidence scalar/structural and free of remote payload text."""
    if isinstance(value, str):
        return value
    return {str(key): _safe_value(item) for key, item in value.items()}


def _safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _redact_text(text: str, token: str | None) -> str:
    if token:
        return text.replace(token, "<redacted>")
    return text


def assert_token_free(value: object, token: str) -> None:
    """Reject a report value that accidentally contains the runtime credential."""
    encoded = json.dumps(value, sort_keys=True, default=str)
    if token and token in encoded:
        raise VerificationError("credential appeared in verifier evidence")


def assert_exact_tool_inventory(tool_names: Sequence[str]) -> tuple[str, ...]:
    """Require exactly the 17 unique names advertised by the source contract."""
    names = tuple(tool_names)
    duplicates = len(names) != len(set(names))
    actual = set(names)
    if duplicates or actual != EXPECTED_TOOL_NAMES or len(names) != len(EXPECTED_TOOLS):
        missing = sorted(EXPECTED_TOOL_NAMES - actual)
        extra = sorted(actual - EXPECTED_TOOL_NAMES)
        duplicate_names = sorted({name for name in names if names.count(name) > 1})
        details = {
            "expected_count": len(EXPECTED_TOOLS),
            "actual_count": len(names),
            "missing": missing,
            "extra": extra,
            "duplicates": duplicate_names,
        }
        raise VerificationError(
            f"MCP tools/list did not match the exact 17-tool inventory: {json.dumps(details, sort_keys=True)}",
            operation="MCP tools/list",
        )
    return names


def listed_tool_names(response: Mapping[str, Any], operation: str = "MCP tools/list") -> tuple[str, ...]:
    """Extract tool names from a JSON-RPC ``tools/list`` response."""
    result = _result_object(response, operation)
    tools = result.get("tools")
    if not isinstance(tools, list):
        raise VerificationError(f"{operation} did not return a tools list", operation=operation)
    names: list[str] = []
    for item in tools:
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
            raise VerificationError(
                f"{operation} returned a malformed tool entry", operation=operation
            )
        names.append(item["name"])
    return assert_exact_tool_inventory(names)


def _initialize_params() -> dict[str, Any]:
    return {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {
            "name": "tasca-remote-mcp-full-matrix-verifier",
            "version": RELEASE_VERSION,
        },
    }


def _unauthenticated_mcp_check(
    mcp_url: str,
    transport: Transport | None,
) -> None:
    """Require unauthenticated MCP initialization to be rejected with HTTP 401."""
    send = transport or request
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _initialize_params()},
        separators=(",", ":"),
    ).encode()
    status, _body, _headers = send(
        mcp_url,
        "unauthenticated MCP initialize",
        token=None,
        method="POST",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    if status != 401:
        raise VerificationError(
            f"unauthenticated MCP initialize returned HTTP {status}; expected 401",
            operation="unauthenticated MCP initialize",
        )


def _require_admin_initialize(client: MCPClient) -> tuple[dict[str, Any], tuple[str, ...]]:
    initialized = client.rpc(
        "initialize",
        _initialize_params(),
        operation="Admin MCP initialize",
    )
    result = _result_object(initialized, "Admin MCP initialize")
    client.rpc(
        "notifications/initialized",
        {},
        operation="Admin MCP initialized notification",
        notification=True,
    )
    tools_response = client.rpc(
        "tools/list", {}, operation="Admin MCP tools/list"
    )
    names = listed_tool_names(tools_response)
    return dict(result), names


def _health_preflight(
    base_url: str,
    expected_version: str,
    transport: Transport | None,
) -> dict[str, Any]:
    """Verify public health and exact release/viewer-auth configuration."""
    health = request_json(
        f"{base_url}/api/v1/health",
        "public health",
        transport=transport,
    )
    observed_version = health.get("version")
    if observed_version != expected_version:
        raise VerificationError(
            "public health did not report the expected release version",
            operation="public health",
        )
    if health.get("viewer_auth_required") is not True:
        raise VerificationError(
            "public health did not report viewer_auth_required=true",
            operation="public health",
        )
    return {
        "version": observed_version,
        "viewer_auth_required": True,
    }


def _admin_role_preflight(
    base_url: str,
    token: str,
    transport: Transport | None,
) -> None:
    """Validate the supplied runtime credential as an Admin credential."""
    auth = request_json(
        f"{base_url}/api/v1/auth/validate",
        "Admin credential validation",
        token=token,
        transport=transport,
    )
    if auth.get("role") != "admin":
        raise VerificationError(
            "runtime credential did not validate as admin",
            operation="Admin credential validation",
        )


def _discover_existing_patrons(
    client: MCPClient,
    reference_table: str,
    matrix: ToolMatrix,
) -> tuple[PatronReference, PatronReference]:
    """Select two distinct existing patrons without mutating the reference table."""
    seats_data = call_tool(
        client,
        "seat_list",
        {"table_id": reference_table, "active_only": False},
    )
    seats = extract_seats(seats_data, "reference seat_list")
    patron_ids: list[str] = []
    for seat in seats:
        patron_id = seat.get("patron_id")
        if isinstance(patron_id, str) and patron_id and patron_id not in patron_ids:
            patron_ids.append(patron_id)
    if len(patron_ids) < 2:
        raise VerificationError(
            "reference table did not expose two distinct existing patrons",
            operation="reference seat_list",
        )

    patrons: list[PatronReference] = []
    for patron_id in patron_ids[:2]:
        patron_data = call_tool(client, "patron_get", {"patron_id": patron_id})
        patron = extract_patron(patron_data, "reference patron_get")
        if patron.patron_id != patron_id:
            raise VerificationError(
                "reference patron_get returned a different patron ID",
                operation="reference patron_get",
            )
        patrons.append(patron)
        matrix.pass_tool(
            "patron_get",
            {"patron_id": patron_id, "reference_table": reference_table},
        )

    # The display-name path is intentional: passing patron_id would not prove
    # the public return-existing compatibility behavior and could create data.
    registered_data = call_tool(
        client,
        "patron_register",
        {"display_name": patrons[0].display_name},
    )
    registered = extract_patron(registered_data, "existing-display-name patron_register")
    if registered.patron_id != patrons[0].patron_id or registered_data.get("is_new") is not False:
        raise VerificationError(
            "patron_register did not return the existing display-name patron",
            operation="existing-display-name patron_register",
        )
    matrix.pass_tool(
        "patron_register",
        {"existing_display_name": True, "is_new": False},
    )
    return patrons[0], patrons[1]


def _cleanup_readiness(
    client: MCPClient,
    matrix: ToolMatrix,
) -> None:
    """Authorize the three cleanup operations against a guaranteed-absent ID."""
    sentinel = f"mcp-full-matrix-absent-{uuid.uuid4()}"
    table_get_code = expect_tool_error(
        client, "table_get", {"table_id": sentinel}, {"NOT_FOUND"}
    )
    matrix.pass_tool(
        "table_get",
        {"preflight_absence": table_get_code},
    )

    control_code = expect_tool_error(
        client,
        "table_control",
        {
            "table_id": sentinel,
            "action": "close",
            "speaker_name": "mcp-full-matrix-preflight",
        },
        {"NOT_FOUND"},
    )
    matrix.pass_tool(
        "table_control",
        {"preflight_absence": control_code},
    )

    delete_code = expect_tool_error(
        client,
        "table_delete_batch",
        {"ids": []},
        {"INVALID_REQUEST"},
    )
    matrix.pass_tool(
        "table_delete_batch",
        {"preflight_empty_batch": delete_code},
    )


def preflight(
    base_url: str,
    expected_version: str,
    reference_table: str,
    token: str,
    *,
    transport: Transport | None = None,
    matrix: ToolMatrix | None = None,
) -> tuple[MCPClient, tuple[PatronReference, PatronReference], dict[str, Any], ToolMatrix]:
    """Run every no-table-effect gate and return an initialized Admin client."""
    if expected_version != RELEASE_VERSION:
        raise VerificationError(
            f"verifier expected version must be {RELEASE_VERSION}",
            operation="configuration",
        )
    if not reference_table:
        raise VerificationError("reference table is required", operation="configuration")
    report_matrix = matrix or ToolMatrix()
    health = _health_preflight(base_url, expected_version, transport)
    _admin_role_preflight(base_url, token, transport)
    client = MCPClient(base_url, token, transport)
    _unauthenticated_mcp_check(client.mcp_url, transport)
    server_info, names = _require_admin_initialize(client)
    # Resetting a proxy with no arguments is safe and proves the direct
    # endpoint's local connection contract before any table effect.
    connect_data = call_tool(client, "connect", {})
    if connect_data.get("mode") != "local":
        raise VerificationError(
            "MCP connect with no arguments did not return local mode",
            operation="MCP connect",
        )
    report_matrix.pass_tool("connect", {"arguments": {}, "mode": "local"})
    status_data = call_tool(client, "connection_status", {})
    if status_data.get("mode") != "local" or status_data.get("is_healthy") is not True:
        raise VerificationError(
            "MCP connection_status did not report healthy local mode",
            operation="MCP connection_status",
        )
    report_matrix.pass_tool(
        "connection_status",
        {"arguments": {}, "mode": "local", "is_healthy": True},
    )
    patrons = _discover_existing_patrons(client, reference_table, report_matrix)
    # Discovery is authoritative only after exact inventory and cleanup probes.
    _cleanup_readiness(client, report_matrix)
    return (
        client,
        patrons,
        {
            "health": health,
            "server_info": {
                key: value
                for key, value in server_info.items()
                if key in {"name", "version"} and isinstance(value, (str, int, float, bool))
            },
            "tool_inventory": {"count": len(names), "exact": True},
            "unauthenticated_mcp": {"status": 401},
            "selected_patrons": {
                "count": len(patrons),
                "ids": [patron.patron_id for patron in patrons],
            },
            "cleanup_authorized": True,
            "connection": {"mode": "local", "is_healthy": True},
        },
        report_matrix,
    )


def _table_status(data: Mapping[str, Any], operation: str) -> str:
    table = data.get("table")
    source = table if isinstance(table, Mapping) else data
    status = source.get("status")
    if not isinstance(status, str) or not status:
        raise VerificationError(f"{operation} omitted table status", operation=operation)
    return status.lower()


def _table_version(data: Mapping[str, Any], operation: str) -> int:
    table = data.get("table")
    source = table if isinstance(table, Mapping) else data
    version = source.get("version")
    if not isinstance(version, int):
        raise VerificationError(f"{operation} omitted table version", operation=operation)
    return version


def _require_table_id(data: Mapping[str, Any], expected: str, operation: str) -> None:
    if extract_table_id(data, operation) != expected:
        raise VerificationError(
            f"{operation} returned a different table ID", operation=operation
        )


def _seat_state_is_active(state: object) -> bool:
    return str(state).lower() in {"running", "idle", "joined", "active"}


def _seat_state_is_left(state: object) -> bool:
    return str(state).lower() in {"done", "left", "departed", "expired"}


def _assert_seat_snapshot(
    data: Mapping[str, Any],
    expected_ids: set[str],
    *,
    active_only: bool,
    active_count: int,
    operation: str,
    expected_returned_ids: set[str] | None = None,
) -> list[Mapping[str, Any]]:
    seats = extract_seats(data, operation)
    ids = {
        str(seat.get("patron_id"))
        for seat in seats
        if isinstance(seat.get("patron_id"), str)
    }
    if expected_returned_ids is not None and ids != expected_returned_ids:
        raise VerificationError(
            f"{operation} returned an unexpected patron set", operation=operation
        )
    if expected_returned_ids is None and not ids <= expected_ids:
        raise VerificationError(
            f"{operation} returned an unrelated patron", operation=operation
        )
    observed_count = data.get("active_count")
    if observed_count != active_count:
        raise VerificationError(
            f"{operation} returned active_count {observed_count}; expected {active_count}",
            operation=operation,
        )
    if active_only and len(seats) != active_count:
        raise VerificationError(
            f"{operation} active seat length did not equal active_count",
            operation=operation,
        )
    return seats


def _assert_export(data: Mapping[str, Any], table_id: str, expected_format: str) -> None:
    if data.get("table_id") != table_id or data.get("format") != expected_format:
        raise VerificationError(
            f"table_export returned an unexpected {expected_format} binding",
            operation=f"MCP table_export {expected_format}",
        )
    content = data.get("content")
    if not isinstance(content, str) or not content:
        raise VerificationError(
            f"table_export {expected_format} content was empty",
            operation=f"MCP table_export {expected_format}",
        )
    if expected_format == "jsonl":
        lines = [line for line in content.splitlines() if line.strip()]
        if not lines:
            raise VerificationError(
                "table_export jsonl returned no records",
                operation="MCP table_export jsonl",
            )
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise VerificationError(
                    "table_export jsonl contained invalid JSON",
                    operation="MCP table_export jsonl",
                ) from error
            if not isinstance(record, Mapping):
                raise VerificationError(
                    "table_export jsonl contained a non-object record",
                    operation="MCP table_export jsonl",
                )


def exercise_lifecycle(
    client: MCPClient,
    patrons: tuple[PatronReference, PatronReference],
    expected_version: str,
    wait_ms: int,
    matrix: ToolMatrix,
) -> tuple[str, str]:
    """Exercise all mutating/read tools against one table, then leave it closed."""
    first, second = patrons
    create_data = call_tool(
        client,
        "table_create",
        {
            "title": "Tasca 0.1.31 full MCP matrix verification",
            "context": "Created by the tracked verifier and removed during reconciliation.",
            "created_by": first.patron_id,
            "host_ids": [first.patron_id, second.patron_id],
            "metadata": {"verifier": "remote-mcp-full-matrix", "version": expected_version},
            "policy": {},
            "board": {},
            "dedup_id": f"mcp-full-matrix-{uuid.uuid4()}",
        },
    )
    table_id = extract_table_id(create_data, "MCP table_create")
    client.active_table_id = table_id
    status = _table_status(create_data, "MCP table_create")
    client.active_table_status = status
    if status != "open":
        raise VerificationError(
            f"MCP table_create returned status {status}; expected open",
            operation="MCP table_create",
        )
    matrix.pass_tool("table_create", {"table_id": table_id, "status": status})

    for patron in patrons:
        joined = call_tool(
            client,
            "table_join",
            {
                "table_id": table_id,
                "patron_id": patron.patron_id,
                "history_limit": 10,
                "history_max_bytes": 65536,
            },
        )
        _require_table_id(joined, table_id, "MCP table_join")
        if not isinstance(joined.get("initial"), Mapping):
            raise VerificationError(
                "MCP table_join omitted initial history", operation="MCP table_join"
            )
    matrix.pass_tool("table_join", {"joins": 2, "table_id": table_id})

    fetched = call_tool(client, "table_get", {"table_id": table_id})
    _require_table_id(fetched, table_id, "MCP table_get")
    version = _table_version(fetched, "MCP table_get")
    if _table_status(fetched, "MCP table_get") != "open":
        raise VerificationError("MCP table_get did not report open", operation="MCP table_get")
    matrix.pass_tool("table_get", {"table_id": table_id, "status": "open", "version": version})

    listed = call_tool(client, "table_list", {"status": "all"})
    tables = listed.get("tables")
    if not isinstance(tables, list):
        raise VerificationError("MCP table_list did not return tables", operation="MCP table_list")
    table_ids = {
        item.get("table_id") or item.get("id")
        for item in tables
        if isinstance(item, Mapping)
    }
    if table_id not in table_ids:
        raise VerificationError("MCP table_list omitted the test table", operation="MCP table_list")
    count = listed.get("total_count", listed.get("total"))
    if not isinstance(count, int) or count < len(tables):
        raise VerificationError("MCP table_list returned an invalid count", operation="MCP table_list")
    matrix.pass_tool("table_list", {"status_filter": "all", "contains_test_table": True})

    updated = call_tool(
        client,
        "table_update",
        {
            "table_id": table_id,
            "expected_version": version,
            "patch": {
                "metadata": {"verifier": "remote-mcp-full-matrix", "updated": True},
                "policy": {},
                "board": {},
            },
            "speaker_name": first.display_name,
            "patron_id": first.patron_id,
            "dedup_id": f"mcp-full-matrix-update-{uuid.uuid4()}",
        },
    )
    _require_table_id(updated, table_id, "MCP table_update")
    updated_version = _table_version(updated, "MCP table_update")
    if updated_version != version + 1:
        raise VerificationError(
            "MCP table_update did not increment version", operation="MCP table_update"
        )
    matrix.pass_tool("table_update", {"version_before": version, "version_after": updated_version})

    saying = call_tool(
        client,
        "table_say",
        {
            "table_id": table_id,
            "content": "Representative full-matrix saying.",
            "speaker_kind": "agent",
            "patron_id": first.patron_id,
            "speaker_name": first.display_name,
            "mentions": [second.patron_id],
            "saying_type": "text",
            "dedup_id": f"mcp-full-matrix-say-{uuid.uuid4()}",
        },
    )
    saying_id = _extract_id(saying, "MCP table_say", ("saying_id", "id"))
    sequence = saying.get("sequence")
    if not isinstance(sequence, int) or sequence < 0:
        raise VerificationError("MCP table_say returned an invalid sequence", operation="MCP table_say")
    matrix.pass_tool("table_say", {"saying_id_present": bool(saying_id), "sequence": sequence})

    listened = call_tool(
        client,
        "table_listen",
        {"table_id": table_id, "since_sequence": -1, "limit": 50},
    )
    sayings = extract_sayings(listened, "MCP table_listen")
    if not any(item.get("content") == "Representative full-matrix saying." for item in sayings):
        raise VerificationError(
            "MCP table_listen omitted the representative saying",
            operation="MCP table_listen",
        )
    next_sequence = listened.get("next_sequence")
    if not isinstance(next_sequence, int):
        raise VerificationError(
            "MCP table_listen returned an invalid next_sequence", operation="MCP table_listen"
        )
    matrix.pass_tool("table_listen", {"saying_observed": True, "next_sequence": next_sequence})

    waited = call_tool(
        client,
        "table_wait",
        {
            "table_id": table_id,
            "since_sequence": -1,
            "wait_ms": wait_ms,
            "limit": 50,
            "include_table": True,
        },
    )
    waited_sayings = extract_sayings(waited, "MCP table_wait")
    if not waited_sayings:
        raise VerificationError(
            "MCP table_wait did not return the representative saying",
            operation="MCP table_wait",
        )
    if waited.get("timeout") is True:
        raise VerificationError(
            "MCP table_wait timed out despite available sayings", operation="MCP table_wait"
        )
    matrix.pass_tool(
        "table_wait",
        {"wait_ms": wait_ms, "bounded": wait_ms <= MAX_WAIT_MS, "saying_observed": True},
    )

    markdown = call_tool(
        client, "table_export", {"table_id": table_id, "format": "markdown"}
    )
    _assert_export(markdown, table_id, "markdown")
    jsonl = call_tool(client, "table_export", {"table_id": table_id, "format": "jsonl"})
    _assert_export(jsonl, table_id, "jsonl")
    matrix.pass_tool(
        "table_export",
        {"formats": ["markdown", "jsonl"], "table_id": table_id},
    )

    running = call_tool(
        client,
        "seat_heartbeat",
        {
            "table_id": table_id,
            "patron_id": first.patron_id,
            "state": "running",
            "ttl_ms": 60_000,
            "dedup_id": f"mcp-full-matrix-running-{uuid.uuid4()}",
        },
    )
    idle = call_tool(
        client,
        "seat_heartbeat",
        {
            "table_id": table_id,
            "patron_id": second.patron_id,
            "state": "idle",
            "ttl_ms": 60_000,
            "dedup_id": f"mcp-full-matrix-idle-{uuid.uuid4()}",
        },
    )
    if not isinstance(running.get("expires_at"), str) or not isinstance(idle.get("expires_at"), str):
        raise VerificationError(
            "MCP seat_heartbeat omitted expires_at", operation="MCP seat_heartbeat"
        )

    all_active = call_tool(
        client,
        "seat_list",
        {"table_id": table_id, "active_only": True},
    )
    _assert_seat_snapshot(
        all_active,
        {first.patron_id, second.patron_id},
        active_only=True,
        active_count=2,
        operation="MCP seat_list all-active",
        expected_returned_ids={first.patron_id, second.patron_id},
    )
    all_stored = call_tool(
        client,
        "seat_list",
        {"table_id": table_id, "active_only": False},
    )
    _assert_seat_snapshot(
        all_stored,
        {first.patron_id, second.patron_id},
        active_only=False,
        active_count=2,
        operation="MCP seat_list all-active unfiltered",
        expected_returned_ids={first.patron_id, second.patron_id},
    )

    left_second = call_tool(
        client,
        "seat_heartbeat",
        {
            "table_id": table_id,
            "patron_id": second.patron_id,
            "state": "done",
            "ttl_ms": 60_000,
            "dedup_id": f"mcp-full-matrix-done-second-{uuid.uuid4()}",
        },
    )
    if not isinstance(left_second.get("expires_at"), str):
        raise VerificationError(
            "MCP seat_heartbeat done omitted expires_at", operation="MCP seat_heartbeat"
        )
    mixed_active = call_tool(
        client, "seat_list", {"table_id": table_id, "active_only": True}
    )
    mixed_active_seats = _assert_seat_snapshot(
        mixed_active,
        {first.patron_id, second.patron_id},
        active_only=True,
        active_count=1,
        operation="MCP seat_list mixed active",
        expected_returned_ids={first.patron_id},
    )
    if not all(_seat_state_is_active(seat.get("state")) for seat in mixed_active_seats):
        raise VerificationError(
            "MCP seat_list mixed active returned a non-active seat",
            operation="MCP seat_list mixed active",
        )
    mixed_stored = call_tool(
        client, "seat_list", {"table_id": table_id, "active_only": False}
    )
    mixed_stored_seats = _assert_seat_snapshot(
        mixed_stored,
        {first.patron_id, second.patron_id},
        active_only=False,
        active_count=1,
        operation="MCP seat_list mixed unfiltered",
        expected_returned_ids={first.patron_id, second.patron_id},
    )
    if not any(
        seat.get("patron_id") == second.patron_id and _seat_state_is_left(seat.get("state"))
        for seat in mixed_stored_seats
    ):
        raise VerificationError(
            "MCP seat_list mixed unfiltered omitted the LEFT seat",
            operation="MCP seat_list mixed unfiltered",
        )

    left_first = call_tool(
        client,
        "seat_heartbeat",
        {
            "table_id": table_id,
            "patron_id": first.patron_id,
            "state": "done",
            "ttl_ms": 60_000,
            "dedup_id": f"mcp-full-matrix-done-first-{uuid.uuid4()}",
        },
    )
    if not isinstance(left_first.get("expires_at"), str):
        raise VerificationError(
            "MCP seat_heartbeat all-left omitted expires_at", operation="MCP seat_heartbeat"
        )
    all_left_active = call_tool(
        client, "seat_list", {"table_id": table_id, "active_only": True}
    )
    _assert_seat_snapshot(
        all_left_active,
        {first.patron_id, second.patron_id},
        active_only=True,
        active_count=0,
        operation="MCP seat_list all-LEFT active",
        expected_returned_ids=set(),
    )
    all_left_stored = call_tool(
        client, "seat_list", {"table_id": table_id, "active_only": False}
    )
    all_left_seats = _assert_seat_snapshot(
        all_left_stored,
        {first.patron_id, second.patron_id},
        active_only=False,
        active_count=0,
        operation="MCP seat_list all-LEFT unfiltered",
        expected_returned_ids={first.patron_id, second.patron_id},
    )
    if not all(
        _seat_state_is_left(seat.get("state")) for seat in all_left_seats
    ):
        raise VerificationError(
            "MCP seat_list all-LEFT retained an active seat",
            operation="MCP seat_list all-LEFT unfiltered",
        )
    matrix.pass_tool(
        "seat_heartbeat",
        {"states": ["running", "idle", "done"], "done_calls": 2},
    )
    matrix.pass_tool(
        "seat_list",
        {
            "all_active": {"active_count": 2, "returned": 2},
            "mixed": {"active_count": 1, "returned_active": 1, "returned_all": 2},
            "all_left": {"active_count": 0, "returned_active": 0, "returned_all": 2},
        },
    )

    paused = call_tool(
        client,
        "table_control",
        {
            "table_id": table_id,
            "action": "pause",
            "speaker_name": first.display_name,
            "patron_id": first.patron_id,
            "reason": "full matrix pause",
            "dedup_id": f"mcp-full-matrix-pause-{uuid.uuid4()}",
        },
    )
    if paused.get("table_status") != "paused":
        raise VerificationError("MCP table_control pause did not pause", operation="MCP table_control")
    resumed = call_tool(
        client,
        "table_control",
        {
            "table_id": table_id,
            "action": "resume",
            "speaker_name": first.display_name,
            "patron_id": first.patron_id,
            "reason": "full matrix resume",
            "dedup_id": f"mcp-full-matrix-resume-{uuid.uuid4()}",
        },
    )
    if resumed.get("table_status") != "open":
        raise VerificationError("MCP table_control resume did not open", operation="MCP table_control")
    closed = call_tool(
        client,
        "table_control",
        {
            "table_id": table_id,
            "action": "close",
            "speaker_name": first.display_name,
            "patron_id": first.patron_id,
            "reason": "full matrix close",
            "dedup_id": f"mcp-full-matrix-close-{uuid.uuid4()}",
        },
    )
    if closed.get("table_status") != "closed":
        raise VerificationError("MCP table_control close did not close", operation="MCP table_control")
    client.active_table_status = "closed"
    matrix.pass_tool(
        "table_control",
        {"transitions": ["open->paused", "paused->open", "open->closed"]},
    )

    closed_say_code = expect_tool_error(
        client,
        "table_say",
        {
            "table_id": table_id,
            "content": "closed-state negative probe",
            "speaker_kind": "agent",
            "patron_id": first.patron_id,
            "speaker_name": first.display_name,
        },
        {"OPERATION_NOT_ALLOWED", "TABLE_CLOSED", "INVALID_STATE"},
    )
    matrix.pass_tool(
        "table_say",
        {"closed_state_error": closed_say_code},
    )
    return table_id, "closed"


def _cleanup_table(
    client: MCPClient,
    table_id: str,
    known_status: str | None,
    matrix: ToolMatrix,
) -> dict[str, Any]:
    """Attempt close, exact batch-delete, and post-delete NOT_FOUND once each."""
    cleanup: dict[str, Any] = {
        "status": "failed",
        "table_id": table_id,
        "close": {"attempted": True, "status": "unknown"},
        "delete": {"attempted": True, "status": "unknown"},
        "post_delete": {"attempted": True, "status": "unknown"},
        "residual_table_id": table_id,
        "observed_status": known_status,
    }

    try:
        close_payload = client.raw_tool(
            "table_control",
            {
                "table_id": table_id,
                "action": "close",
                "speaker_name": "mcp-full-matrix-reconciler",
                "reason": "bounded verifier cleanup",
                "dedup_id": f"mcp-full-matrix-reconcile-{uuid.uuid4()}",
            },
        )
        close_code = tool_error_code(close_payload)
        if close_code is None:
            close_data = require_tool_ok(close_payload, "MCP cleanup table_control")
            if close_data.get("table_status") not in (None, "closed"):
                raise VerificationError(
                    "cleanup close returned a non-closed status",
                    operation="MCP cleanup table_control",
                )
            cleanup["close"] = {"attempted": True, "status": "verified"}
        elif close_code in {"OPERATION_NOT_ALLOWED", "INVALID_STATE"} and known_status == "closed":
            cleanup["close"] = {
                "attempted": True,
                "status": "already_closed",
                "error_code": close_code,
            }
        else:
            cleanup["close"] = {
                "attempted": True,
                "status": "failed",
                "error_code": close_code,
            }
    except VerificationError as error:
        cleanup["close"] = {
            "attempted": True,
            "status": "failed",
            "error_code": error.code or "TRANSPORT_OR_PROTOCOL",
        }

    try:
        delete_payload = client.raw_tool(
            "table_delete_batch",
            {"ids": [table_id]},
        )
        delete_data = require_tool_ok(delete_payload, "MCP cleanup table_delete_batch")
        deleted_count = delete_data.get("deleted_count")
        failed = delete_data.get("failed")
        deleted_ids = delete_data.get("deleted_ids")
        if deleted_count != 1 or failed != [] or (
            deleted_ids is not None and deleted_ids != [table_id]
        ):
            raise VerificationError(
                "cleanup batch delete did not delete exactly the test table",
                operation="MCP cleanup table_delete_batch",
            )
        cleanup["delete"] = {
            "attempted": True,
            "status": "verified",
            "deleted_count": 1,
            "failed": [],
        }
        matrix.pass_tool(
            "table_delete_batch",
            {"deleted_count": 1, "failed": [], "exact_ids": True},
        )
    except VerificationError as error:
        cleanup["delete"] = {
            "attempted": True,
            "status": "failed",
            "error_code": error.code or "TRANSPORT_OR_PROTOCOL",
        }

    try:
        post_delete_code = expect_tool_error(
            client,
            "table_get",
            {"table_id": table_id},
            {"NOT_FOUND"},
        )
        cleanup["post_delete"] = {
            "attempted": True,
            "status": "verified",
            "error_code": post_delete_code,
        }
        cleanup["residual_table_id"] = None
        cleanup["status"] = "verified"
        matrix.pass_tool(
            "table_get",
            {"post_delete": post_delete_code},
        )
    except VerificationError as error:
        cleanup["post_delete"] = {
            "attempted": True,
            "status": "failed",
            "error_code": error.code or "TRANSPORT_OR_PROTOCOL",
        }

    if cleanup["status"] != "verified":
        cleanup["status"] = "failed"
    return cleanup


def _base_report(
    base_url: str,
    expected_version: str,
    reference_table: str,
    matrix: ToolMatrix,
) -> dict[str, Any]:
    return {
        "format": REPORT_FORMAT,
        "status": "FAIL",
        "target": {
            "base_url": base_url,
            "mcp_url": f"{base_url}{MCP_PATH}",
            "expected_version": expected_version,
            "reference_table": reference_table,
        },
        "tools": matrix.as_rows(),
        "first_failure": None,
        "cleanup": {
            "status": "not_needed",
            "table_id": None,
            "residual_table_id": None,
        },
        "preflight": {},
    }


def write_report(path: Path, report: Mapping[str, Any], token: str | None = None) -> None:
    """Atomically write a token-free JSON report with exact mode 0600."""
    assert_token_free(report, token or "")
    encoded = json.dumps(report, sort_keys=True, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(encoded, encoding="utf-8")
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, path)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    finally:
        if temporary.exists():
            temporary.unlink()
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise VerificationError("verification report must have mode 0600", operation="report")


def run_verification(
    base_url: str,
    expected_version: str,
    reference_table: str,
    token: str,
    report_path: Path | None = None,
    wait_ms: int = DEFAULT_WAIT_MS,
    *,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Run the matrix, write its report, and raise on a failed verification."""
    matrix = ToolMatrix()
    normalized_base = require_https_base_url(base_url)
    report = _base_report(normalized_base, expected_version, reference_table, matrix)
    table_id: str | None = None
    known_status: str | None = None
    cleanup: dict[str, Any] | None = None

    try:
        if not isinstance(wait_ms, int) or isinstance(wait_ms, bool) or not 0 <= wait_ms <= MAX_WAIT_MS:
            raise VerificationError(
                f"wait bound must be between 0 and {MAX_WAIT_MS} milliseconds",
                operation="configuration",
            )
        client, patrons, preflight_data, matrix = preflight(
            normalized_base,
            expected_version,
            reference_table,
            token,
            transport=transport,
            matrix=matrix,
        )
        report["tools"] = matrix.as_rows()
        report["preflight"] = preflight_data
        table_id, known_status = exercise_lifecycle(
            client, patrons, expected_version, wait_ms, matrix
        )
        cleanup = _cleanup_table(client, table_id, known_status, matrix)
        report["cleanup"] = cleanup
        report["tools"] = matrix.as_rows()
        if cleanup["status"] != "verified":
            raise BlockedVerification(
                "table cleanup did not prove post-delete NOT_FOUND",
                operation="cleanup",
            )
        report["status"] = "PASS"
        report["tools"] = matrix.as_rows()
        if report_path is not None:
            write_report(report_path, report, token)
        return report
    except VerificationError as error:
        if isinstance(error, BlockedVerification):
            report["status"] = "BLOCKED"
        client_for_cleanup = locals().get("client")
        if table_id is None and isinstance(client_for_cleanup, MCPClient):
            table_id = client_for_cleanup.active_table_id
            known_status = client_for_cleanup.active_table_status
        failed_tool = next(
            (name for name in EXPECTED_TOOLS if name in error.operation),
            None,
        )
        if failed_tool is not None:
            matrix.fail_tool(
                failed_tool,
                {"operation": error.operation, "code": error.code},
            )
        matrix.pending_after_failure(error.operation)
        report["tools"] = matrix.as_rows()
        report["first_failure"] = {
            "operation": error.operation,
            "code": error.code,
            "message": _redact_text(str(error), token),
        }
        if table_id is not None and cleanup is None:
            # The client exists if a table ID has been recorded.  The local
            # variable is intentionally recovered from the preflight scope.
            if isinstance(client_for_cleanup, MCPClient):
                cleanup = _cleanup_table(
                    client_for_cleanup,
                    table_id,
                    known_status,
                    matrix,
                )
                report["cleanup"] = cleanup
                report["tools"] = matrix.as_rows()
                if cleanup["status"] != "verified":
                    report["status"] = "BLOCKED"
                    error = BlockedVerification(
                        "table cleanup did not prove post-delete NOT_FOUND",
                        operation="cleanup",
                    )
            else:
                report["cleanup"] = {
                    "status": "unknown",
                    "table_id": table_id,
                    "residual_table_id": table_id,
                }
                report["status"] = "BLOCKED"
        if report_path is not None:
            write_report(report_path, report, token)
        if isinstance(error, BlockedVerification) or report["status"] == "BLOCKED":
            raise BlockedVerification(str(error), operation=error.operation, code=error.code) from error
        raise VerificationError(str(error), operation=error.operation, code=error.code) from error


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser without reading credentials or making requests."""
    parser = argparse.ArgumentParser(
        description="Verify the public HTTPS Tasca MCP 17-tool matrix with bounded cleanup."
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="HTTPS base URL; defaults to TASCA_HTTPS_BASE_URL",
    )
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--reference-table", required=True)
    parser.add_argument(
        "--report",
        "--report-path",
        dest="report_path",
        required=True,
        type=Path,
        help="mode-0600 JSON report path",
    )
    parser.add_argument(
        "--wait-ms",
        "--wait-bound",
        dest="wait_ms",
        type=int,
        default=DEFAULT_WAIT_MS,
        help=f"bounded table_wait duration in milliseconds (0-{MAX_WAIT_MS})",
    )
    parser.add_argument(
        "--credential-env",
        "--token-env",
        dest="credential_env",
        default=DEFAULT_CREDENTIAL_ENV,
        help=f"environment variable containing the existing credential (default: {DEFAULT_CREDENTIAL_ENV})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and emit only a redacted one-line result."""
    parser = build_parser()
    args = parser.parse_args(argv)
    report_path = args.report_path
    try:
        raw_base_url = args.base_url or os.environ.get("TASCA_HTTPS_BASE_URL")
        if not raw_base_url:
            raise VerificationError("TASCA_HTTPS_BASE_URL is required", operation="configuration")
        base_url = require_https_base_url(raw_base_url)
        token = load_runtime_credential(args.credential_env)
        report = run_verification(
            base_url,
            args.expected_version,
            args.reference_table,
            token,
            report_path,
            args.wait_ms,
        )
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "report": str(report_path),
                    "tool_rows": len(report["tools"]),
                    "cleanup": report["cleanup"]["status"],
                },
                sort_keys=True,
            )
        )
        return 0
    except VerificationError as error:
        # If validation failed before run_verification could write a report,
        # retain a bounded failure receipt when the path is usable.
        try:
            if not report_path.exists():
                fallback = _base_report(
                    require_https_base_url(args.base_url or os.environ.get("TASCA_HTTPS_BASE_URL", "https://invalid")),
                    args.expected_version,
                    args.reference_table,
                    ToolMatrix(),
                )
                fallback["status"] = "BLOCKED" if isinstance(error, BlockedVerification) else "FAIL"
                fallback["first_failure"] = {
                    "operation": error.operation,
                    "code": error.code,
                    "message": str(error),
                }
                write_report(report_path, fallback)
        except (OSError, ValueError, VerificationError):
            print("verification failure report could not be written", file=sys.stderr)
        print(
            f"remote MCP full matrix verification failed during {error.operation}; report={report_path}",
            file=sys.stderr,
        )
        return 1
    except Exception:  # pragma: no cover - defensive CLI boundary
        print(
            f"remote MCP full matrix verification failed during configuration; report={report_path}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
