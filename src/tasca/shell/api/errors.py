"""HTTP error envelope helpers for the REST API."""

from __future__ import annotations

from typing import Any, NoReturn

from tasca.core.services.http_error_service import (
    error_envelope,
    exception_detail_to_envelope,
)
from tasca.shell.api.fastapi_compat import HTTPException

__all__ = ["error_envelope", "exception_detail_to_envelope", "raise_http_error"]


# @shell:entry - FastAPI exception adapter must raise HTTPException/NoReturn.
# @shell_orchestration: Raises FastAPI HTTPException with standard public error payload.
def raise_http_error(
    http_status: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> NoReturn:
    """Raise an HTTPException whose detail is already standard-shaped."""
    raise HTTPException(status_code=http_status, detail=error_envelope(code, message, details))
