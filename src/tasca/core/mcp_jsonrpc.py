"""Pure helpers for MCP JSON-RPC response parsing and validation."""

from __future__ import annotations

import json
from typing import Any

import deal


@deal.pre(lambda text: len(text.strip()) > 0)
@deal.post(lambda result: isinstance(result, bool))
def _is_parseable_payload(text: str) -> bool:
    """Check whether input is parseable as supported JSON or SSE payload."""
    if text.startswith("event:"):
        in_message_event = False
        for line in text.split("\n"):
            if line.startswith("event:"):
                in_message_event = line.split(":", 1)[1].strip() == "message"
            elif line.startswith("data:") and in_message_event:
                payload = line[5:].strip()
                if len(payload) == 0:
                    return False
                try:
                    json.loads(payload)
                    return True
                except json.JSONDecodeError:
                    return False
        return False

    try:
        json.loads(text)
        return True
    except json.JSONDecodeError:
        return False


@deal.pre(lambda text: _is_parseable_payload(text))
@deal.post(lambda result: isinstance(result, (dict, list, str, int, float, bool, type(None))))
def parse_sse_or_json(text: str) -> Any:
    """Parse response body that may be SSE or plain JSON.

    FastMCP streamable HTTP often returns SSE frames of the form:
        event: message
        data: {"jsonrpc": "2.0", ...}

    Args:
        text: Response body text.

    Returns:
        Parsed JSON payload.

    Raises:
        json.JSONDecodeError: If input is neither valid SSE payload nor JSON.

    Examples:
        >>> parse_sse_or_json('{"ok": true}')
        {'ok': True}
        >>> parse_sse_or_json('event: message\\ndata: {"ok": true}\\n\\n')
        {'ok': True}
    """
    if text.startswith("event:"):
        in_message_event = False
        for line in text.split("\n"):
            if line.startswith("event:"):
                in_message_event = line.split(":", 1)[1].strip() == "message"
            elif line.startswith("data:") and in_message_event:
                return json.loads(line[5:].strip())
    return json.loads(text)


@deal.pre(
    lambda data, expected_id: (
        isinstance(data, (dict, list, str, int, float, bool, type(None)))
        and len(expected_id.strip()) > 0
    )
)
@deal.post(
    lambda result: (
        result is None
        or (
            isinstance(result, dict)
            and set(result.keys()) == {"field", "reason"}
            and isinstance(result["field"], str)
            and isinstance(result["reason"], str)
        )
    )
)
def validate_jsonrpc_response(data: Any, expected_id: str) -> dict[str, str] | None:
    """Validate JSON-RPC 2.0 response shape.

    Args:
        data: Parsed JSON candidate.
        expected_id: Request ID expected in the response.

    Returns:
        None when valid, else an error descriptor with field and reason.

    Examples:
        >>> validate_jsonrpc_response({"jsonrpc": "2.0", "id": "1", "result": {}}, "1")
        >>> validate_jsonrpc_response("not a dict", "1")
        {'field': 'root', 'reason': 'response must be a dict'}
    """
    if not isinstance(data, dict):
        return {"field": "root", "reason": "response must be a dict"}

    if "jsonrpc" not in data:
        return {"field": "jsonrpc", "reason": "missing required field"}
    if data["jsonrpc"] != "2.0":
        return {"field": "jsonrpc", "reason": 'must be "2.0"'}

    if "id" not in data:
        return {"field": "id", "reason": "missing required field"}
    if data["id"] != expected_id:
        return {"field": "id", "reason": "response id does not match request id"}

    has_result = "result" in data
    has_error = "error" in data
    if not has_result and not has_error:
        return {"field": "result/error", "reason": "must have exactly one of result or error"}
    if has_result and has_error:
        return {"field": "result/error", "reason": "must have exactly one of result or error"}

    if has_error:
        error = data["error"]
        if not isinstance(error, dict):
            return {"field": "error", "reason": "error must be a dict"}
        if "code" not in error or "message" not in error:
            return {
                "field": "error.message",
                "reason": "error must have code (int) and message (str)",
            }
        if not isinstance(error["code"], int):
            return {"field": "error.code", "reason": "error code must be an integer"}
        if not isinstance(error["message"], str):
            return {"field": "error.message", "reason": "error message must be a string"}

    return None
