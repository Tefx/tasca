"""
Patrons API routes.

Endpoints for patron registration and management.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from tasca.shell.api.fastapi_compat import APIRouter, Depends, status
from pydantic import BaseModel
from returns.result import Failure

from tasca.core.domain.patron import PatronId
from tasca.shell.api.deps import get_db
from tasca.shell.api.errors import raise_http_error
from tasca.shell.services.operations.patron_registration import (
    PatronCreateError,
    PatronIdempotencyError,
    PatronLookupError,
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


# @invar:allow shell_result: FastAPI response model adapter, not reusable domain logic.
# @shell_orchestration: HTTP response shaping for shared patron registration outcome.
def _registration_outcome_to_response(outcome: object) -> PatronRegisterResponse:
    """Render a shared patron registration outcome as the REST response model."""
    patron = outcome.patron
    return PatronRegisterResponse(
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
    )


# @shell_orchestration: HTTP-layer mapping from shared operation failures to FastAPI exceptions.
def _raise_registration_error(error: object) -> None:
    """Map shared patron registration failures onto legacy REST error details."""
    if isinstance(error, PatronLookupError):
        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "StorageError", f"Failed to check for existing patron: {error.cause}")
    if isinstance(error, (PatronCreateError, PatronIdempotencyError)):
        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "StorageError", f"Failed to create patron: {error.cause}")
    raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "StorageError", f"Failed to create patron: {error}")


# @invar:allow entry_point_too_thick: patrons.py register_patron_endpoint POST route with docstrings, type hints, and error handling
@router.post("", response_model=PatronRegisterResponse, status_code=status.HTTP_200_OK)
async def register_patron_endpoint(
    data: PatronRegisterRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> PatronRegisterResponse:
    """Register a new patron with deduplication.

    Deduplication is based on the patron display_name. If a patron with the same
    display_name already exists, the existing patron is returned with is_new=False.

    Args:
        data: Patron creation data with display_name and optional kind.
        conn: Database connection (injected via dependency).

    Returns:
        The created or existing patron with is_new flag.

    Raises:
        HTTPException: 500 if database operation fails.
    """
    resolved_name = data.display_name or data.name
    if resolved_name is None:
        raise_http_error(
            status.HTTP_400_BAD_REQUEST,
            "InvalidRequest",
            "display_name (or name for backward compatibility) is required",
        )
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
        _raise_registration_error(result.failure())
    return _registration_outcome_to_response(result.unwrap())


# =============================================================================
# GET /patrons/{patron_id} - Get a patron by ID
# =============================================================================


# @invar:allow entry_point_too_thick: patrons.py get_patron_endpoint GET route with docstrings, type hints, and error handling
@router.get("/{patron_id}")
async def get_patron_endpoint(
    patron_id: str,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, object]:
    """Get a patron by ID.

    Args:
        patron_id: The patron identifier.
        conn: Database connection (injected via dependency).

    Returns:
        The requested patron.

    Raises:
        HTTPException: 404 if patron not found.
        HTTPException: 500 if database operation fails.
    """
    result = get_patron(conn, PatronId(patron_id))

    if isinstance(result, Failure):
        error = result.failure()
        if isinstance(error, PatronNotFoundError):
            raise_http_error(status.HTTP_404_NOT_FOUND, "PatronNotFound", f"Patron not found: {patron_id}")
        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR, "StorageError", f"Failed to get patron: {error}")

    patron = result.unwrap()
    return {
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
    }
