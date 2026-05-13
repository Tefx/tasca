"""Pure HTTP error-envelope shaping for shell framework adapters.

The shell API layer raises framework exceptions and writes responses, but the
public error payload itself is deterministic data transformation.
"""

from __future__ import annotations

from typing import Any

import deal


@deal.pre(lambda code, message, details=None: bool(code) and bool(message))
@deal.post(lambda result: isinstance(result, dict) and isinstance(result.get("error"), dict))
def error_envelope(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the normative REST error envelope.

    >>> error_envelope("InvalidRequest", "Bad input")
    {'error': {'code': 'InvalidRequest', 'message': 'Bad input', 'details': {}}}
    >>> error_envelope("PermissionDenied", "No", {"scope": "admin"})["error"]["details"]
    {'scope': 'admin'}
    """
    return {"error": {"code": code, "message": message, "details": details or {}}}


@deal.pre(lambda status_code, detail: status_code >= 100)
@deal.post(lambda result: isinstance(result, dict) and isinstance(result.get("error"), dict))
def exception_detail_to_envelope(status_code: int, detail: Any) -> dict[str, Any]:
    """Normalize arbitrary FastAPI/Starlette exception detail to a standard envelope.

    >>> exception_detail_to_envelope(404, "missing")["error"]["code"]
    'TableNotFound'
    >>> exception_detail_to_envelope(422, [{"loc": ["body"]}])["error"]["message"]
    'Request validation failed'
    """
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        error = detail["error"]
        return error_envelope(
            str(error.get("code") or _status_code_name(status_code)),
            str(error.get("message") or "Request failed"),
            error.get("details") if isinstance(error.get("details"), dict) else {},
        )
    if isinstance(detail, dict) and "error" in detail and "details" in detail:
        return error_envelope(str(detail["error"]), "Request failed", {"details": detail["details"]})
    if isinstance(detail, dict):
        return error_envelope(_status_code_name(status_code), "Request failed", detail)
    if isinstance(detail, list):
        return error_envelope("InvalidRequest", "Request validation failed", {"errors": detail})
    return error_envelope(_status_code_name(status_code), str(detail), {})


@deal.pre(lambda status_code: status_code >= 100)
@deal.post(lambda result: bool(result))
def _status_code_name(status_code: int) -> str:
    """Return the public API error code for an HTTP status.

    >>> _status_code_name(401)
    'PermissionDenied'
    >>> _status_code_name(500)
    'StorageError'
    """
    if status_code == 401:
        return "PermissionDenied"
    if status_code == 403:
        return "PermissionDenied"
    if status_code == 404:
        return "TableNotFound"
    if status_code == 409:
        return "InvalidState"
    if status_code == 422:
        return "InvalidRequest"
    return "InvalidRequest" if status_code < 500 else "StorageError"
