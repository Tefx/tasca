"""Legacy CLI helper surface used by tests and external callers."""

from __future__ import annotations

import json
import subprocess
from typing import IO, Any, cast

import httpx
from returns.result import Failure, Result, Success


def is_server_running(base_url: str) -> Result[bool, str]:
    """Check whether a Tasca HTTP server is reachable."""
    try:
        with httpx.Client(timeout=2.0) as client:
            response = client.get(f"{base_url.rstrip('/')}/api/v1/health")
    except httpx.HTTPError:
        return Success(False)
    return Success(response.status_code == 200)


def create_table_via_rest(
    question: str,
    context: str | None,
    base_url: str,
    admin_token: str,
) -> Result[dict[str, Any], str]:
    """Create a table through the Tasca REST API."""
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                f"{base_url.rstrip('/')}/api/v1/tables",
                json={"question": question, "context": context},
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        return Failure(f"Failed to create table via REST: {exc}")

    data = response.json()
    if not isinstance(data, dict):
        return Failure("REST create table response was not an object")
    return Success(cast(dict[str, Any], data))


def _mcp_create_request(question: str, context: str | None) -> Result[dict[str, object], str]:
    """Build the JSON-RPC request for the MCP table_create tool."""
    return Success({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "table_create",
            "arguments": {"question": question, "context": context},
        },
    })


def _write_json_line(stdin: IO[str], payload: dict[str, object]) -> None:
    """Write one JSON-RPC line to an MCP stdin stream."""
    stdin.write(json.dumps(payload) + "\n")
    stdin.flush()


def _communicate_create_request(
    process: subprocess.Popen[str],
    question: str,
    context: str | None,
) -> Result[str, str]:
    """Initialize MCP stdio and return the raw table_create response line."""
    if process.stdin is None or process.stdout is None:
        return Failure("MCP process did not expose stdio pipes")

    try:
        _write_json_line(process.stdin, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        process.stdout.readline()
        _write_json_line(process.stdin, _mcp_create_request(question, context).unwrap())
        return Success(process.stdout.readline())
    except OSError as exc:
        return Failure(f"Failed to communicate with MCP server: {exc}")


def _close_mcp_process(process: subprocess.Popen[str]) -> None:
    """Close MCP stdin and terminate the subprocess."""
    if process.stdin is not None:
        process.stdin.close()
    process.terminate()


def _response_text(response: dict[str, Any]) -> Result[str, str]:
    """Extract the text content from an MCP JSON-RPC response object."""
    if "error" in response:
        return Failure(f"MCP table_create failed: {response['error']}")
    try:
        return Success(cast(str, response["result"]["content"][0]["text"]))
    except (KeyError, IndexError, TypeError) as exc:
        return Failure(f"MCP response content text was missing: {exc}")


def _decode_table_payload(text: str) -> Result[dict[str, Any], str]:
    """Decode table data from MCP response text."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return Failure(f"Failed to decode MCP response text: {exc}")

    if not isinstance(payload, dict):
        return Failure("MCP response text was not an object")
    data = payload.get("data")
    return Success(cast(dict[str, Any], data if isinstance(data, dict) else payload))


def _decode_mcp_response(raw_response: str) -> Result[dict[str, Any], str]:
    """Decode table data from a raw MCP JSON-RPC response line."""
    try:
        response = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        return Failure(f"Failed to decode MCP response: {exc}")

    if not isinstance(response, dict):
        return Failure("MCP response was not an object")
    text_result = _response_text(response)
    if isinstance(text_result, Failure):
        return Failure(text_result.failure())
    return _decode_table_payload(text_result.unwrap())


def create_table_via_mcp(question: str, context: str | None) -> Result[dict[str, Any], str]:
    """Create a table through the stdio MCP server."""
    try:
        process = subprocess.Popen(
            ["tasca-mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        return Failure(f"Failed to start MCP server: {exc}")

    try:
        raw_result = _communicate_create_request(process, question, context)
    finally:
        _close_mcp_process(process)

    if isinstance(raw_result, Failure):
        return Failure(raw_result.failure())
    return _decode_mcp_response(raw_result.unwrap())
