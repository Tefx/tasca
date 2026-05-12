"""HTTP error envelope helpers for the REST API."""

from __future__ import annotations

from typing import Any

from tasca.shell.api.fastapi_compat import HTTPException, status


# @invar:allow shell_result: HTTP transport envelope helper returns JSON primitives, not Result.
def error_envelope(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the normative REST error envelope."""
    return {"error": {"code": code, "message": message, "details": details or {}}}


# @shell_orchestration: Raises FastAPI HTTPException with standard public error payload.
def raise_http_error(
    http_status: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Raise an HTTPException whose detail is already standard-shaped."""
    raise HTTPException(status_code=http_status, detail=error_envelope(code, message, details))


# @invar:allow shell_result: FastAPI exception adapter returns JSON primitives for handlers.
# @shell_complexity: Normalizes legacy FastAPI detail variants into one public envelope.
def exception_detail_to_envelope(status_code: int, detail: Any) -> dict[str, Any]:
    """Normalize arbitrary FastAPI/Starlette exception detail to standard envelope."""
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


# @invar:allow shell_result: HTTP status mapper returns a string code for envelope shaping.
# @shell_complexity: Explicit status-to-code mapping keeps auth/not-found/conflict semantics visible.
def _status_code_name(status_code: int) -> str:
    if status_code == status.HTTP_401_UNAUTHORIZED:
        return "PermissionDenied"
    if status_code == status.HTTP_403_FORBIDDEN:
        return "PermissionDenied"
    if status_code == status.HTTP_404_NOT_FOUND:
        return "TableNotFound"
    if status_code == status.HTTP_409_CONFLICT:
        return "InvalidState"
    if status_code == status.HTTP_422_UNPROCESSABLE_ENTITY:
        return "InvalidRequest"
    return "InvalidRequest" if status_code < 500 else "StorageError"
