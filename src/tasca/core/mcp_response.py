"""
MCP response envelope builders.

This module provides pure logic for constructing MCP response envelopes.
Moved from shell layer for @pre/@post contract compliance.
"""

from __future__ import annotations

from typing import Any

import deal


@deal.pre(
    lambda code, message, details=None: (
        isinstance(code, str)
        and isinstance(message, str)
        and len(code) > 0
        and len(message) > 0
        and (details is None or isinstance(details, dict))
    ),
    message="code and message must be non-empty strings, details must be dict if provided",
)
@deal.post(lambda result: result["ok"] is False)
@deal.post(lambda result: "error" in result)
def error_response(
    code: str, message: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Create a standardized error envelope.

    Args:
        code: Error code (e.g., "NOT_FOUND", "VALIDATION_ERROR").
        message: Human-readable error message.
        details: Optional additional error details.

    Returns:
        Error envelope dictionary.

    Examples:
        >>> error_response("NOT_FOUND", "Item not found")
        {'ok': False, 'error': {'code': 'NOT_FOUND', 'message': 'Item not found'}}
        >>> error_response("ERROR", "Failed", {"field": "name"})
        {'ok': False, 'error': {'code': 'ERROR', 'message': 'Failed', 'details': {'field': 'name'}}}
    """
    result: dict[str, Any] = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
    }
    if details:
        result["error"]["details"] = details
    return result


@deal.post(lambda result: result["ok"] is True)
@deal.post(lambda result: "data" in result)
def success_response(data: dict[str, Any]) -> dict[str, Any]:
    """Create a standardized success envelope.

    Args:
        data: The response data.

    Returns:
        Success envelope dictionary.

    Examples:
        >>> success_response({"id": "123"})
        {'ok': True, 'data': {'id': '123'}}
    """
    return {"ok": True, "data": data}
