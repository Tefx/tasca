"""
MCP response envelope helpers.

This module provides standardized response formatting for MCP tools.
Pure helper functions (no I/O).
"""

from __future__ import annotations

from typing import Any


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

    >>> error_response("NOT_FOUND", "Item not found")
    {'ok': False, 'error': {'code': 'NOT_FOUND', 'message': 'Item not found'}}
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


def success_response(data: dict[str, Any]) -> dict[str, Any]:
    """Create a standardized success envelope.

    Args:
        data: The response data.

    Returns:
        Success envelope dictionary.

    >>> success_response({"id": "123"})
    {'ok': True, 'data': {'id': '123'}}
    """
    return {"ok": True, "data": data}
