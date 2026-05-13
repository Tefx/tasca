"""
Patrons API routes.

Endpoints for patron registration and management.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from pydantic import BaseModel
from returns.result import Failure, Result, Success

from tasca.core.domain.patron import PatronId
from tasca.shell.api.deps import get_db
from tasca.shell.api.errors import error_envelope, raise_http_error
from tasca.shell.api.fastapi_compat import APIRouter, Depends, HTTPException, status
from tasca.shell.services.operations.patron_registration import (
    PatronCreateError,
    PatronIdempotencyError,
    PatronLookupError,
    PatronRegistrationOutcome,
    register_patron,
)
from tasca.shell.storage.patron_repo import PatronNotFoundError, get_patron

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================


class PatronRegisterResponse(BaseModel):
    """Response model for patron registration."""

    patron_id: str
    display_name: str
    alias: str | None = None
    server_ts: datetime
    meta: dict[str, object] | None = None
    id: str
    name: str
    kind: str
    created_at: datetime
    is_new: bool


class PatronRegisterRequest(BaseModel):
    """REST request model matching the MCP patron.register contract."""

    display_name: str | None = None
    name: str | None = None
    kind: str = "agent"
    alias: str | None = None
    meta: dict[str, object] | None = None
    patron_id: str | None = None
    dedup_id: str | None = None


# =============================================================================
# POST /patrons - Register a new patron
# =============================================================================


# @shell_orchestration: HTTP response shaping for shared patron registration outcome.
def _registration_outcome_to_response(
    outcome: PatronRegistrationOutcome,
) -> Result[PatronRegisterResponse, str]:
    """Render a shared patron registration outcome as the REST response model."""
    patron = outcome.patron
    return Success(PatronRegisterResponse(
        patron_id=patron.id,
        display_name=patron.name,
        alias=patron.alias,
        server_ts=patron.created_at,
        meta=patron.meta,
        id=patron.id,
        name=patron.name,
        kind=patron.kind,
        created_at=patron.created_at,
        is_new=outcome.is_new,
    ))


# @shell_orchestration: HTTP-layer mapping from shared operation failures to FastAPI exceptions.
def _raise_registration_error(error: object) -> None:
    """Map shared patron registration failures onto legacy REST error details."""
    if isinstance(error, PatronLookupError):
        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "StorageError", f"Failed to check for existing patron: {error.cause}")
    if isinstance(error, (PatronCreateError, PatronIdempotencyError)):
        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "StorageError", f"Failed to create patron: {error.cause}")
    raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "StorageError", f"Failed to create patron: {error}")


def _registration_error_to_http(error: object) -> Result[HTTPException, str]:
    """Map shared patron registration failures onto REST HTTP exceptions."""
    if isinstance(error, PatronLookupError):
        message = f"Failed to check for existing patron: {error.cause}"
    elif isinstance(error, (PatronCreateError, PatronIdempotencyError)):
        message = f"Failed to create patron: {error.cause}"
    else:
        message = f"Failed to create patron: {error}"
    return Success(HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=error_envelope("StorageError", message),
    ))


def _register_patron_response(
    conn: sqlite3.Connection,
    data: PatronRegisterRequest,
) -> Result[PatronRegisterResponse, HTTPException]:
    """Run patron registration and return the REST response model."""
    resolved_name = data.display_name or data.name
    if resolved_name is None:
        return Failure(HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_envelope(
                "InvalidRequest",
                "display_name (or name for backward compatibility) is required",
            ),
        ))
    result = register_patron(
        conn,
        resolved_name,
        kind=data.kind,
        alias=data.alias,
        meta=data.meta,
        patron_id=data.patron_id,
        dedup_id=data.dedup_id,
    )
    if isinstance(result, Failure):
        mapped = _registration_error_to_http(result.failure())
        if isinstance(mapped, Failure):
            return Failure(HTTPException(status_code=500, detail=mapped.failure()))
        return Failure(mapped.unwrap())
    return _registration_outcome_to_response(result.unwrap()).alt(
        lambda error: HTTPException(status_code=500, detail=error)
    )


def _get_patron_response(
    conn: sqlite3.Connection,
    patron_id: str,
) -> Result[dict[str, object], HTTPException]:
    """Fetch a patron and render the legacy REST response shape."""
    result = get_patron(conn, PatronId(patron_id))
    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, PatronNotFoundError):
            return Failure(HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error_envelope("PatronNotFound", f"Patron not found: {patron_id}"),
            ))
        return Failure(HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_envelope("StorageError", f"Failed to get patron: {error}"),
        ))
    patron = result.unwrap()
    return Success({
        "patron": {
            "patron_id": patron.id,
            "display_name": patron.name,
            "alias": patron.alias,
            "meta": patron.meta,
        },
        "patron_id": patron.id,
        "display_name": patron.name,
        "alias": patron.alias,
        "meta": patron.meta,
        "id": patron.id,
        "name": patron.name,
        "kind": patron.kind,
        "created_at": patron.created_at,
    })


@router.post("", response_model=PatronRegisterResponse, status_code=status.HTTP_200_OK)
async def register_patron_endpoint(
    data: PatronRegisterRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> PatronRegisterResponse:
    """Register a new patron with deduplication."""
    result = _register_patron_response(conn, data)
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()


# =============================================================================
# GET /patrons/{patron_id} - Get a patron by ID
# =============================================================================


@router.get("/{patron_id}")
async def get_patron_endpoint(
    patron_id: str,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    """Get a patron by ID."""
    result = _get_patron_response(conn, patron_id)
    if isinstance(result, Failure):
        raise result.failure()
    return result.unwrap()
