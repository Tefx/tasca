"""
Patrons API routes.

Endpoints for patron registration and management.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from returns.result import Failure, Success

from tasca.core.domain.patron import Patron, PatronCreate, PatronId
from tasca.shell.api.deps import get_db
from tasca.shell.storage.patron_repo import (
    PatronNotFoundError,
    create_patron,
    find_patron_by_name,
    get_patron,
)

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================


class PatronRegisterResponse(BaseModel):
    """Response model for patron registration."""

    id: str
    name: str
    kind: str
    created_at: datetime
    is_new: bool


# =============================================================================
# POST /patrons - Register a new patron
# =============================================================================


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
@router.post("", response_model=PatronRegisterResponse, status_code=status.HTTP_200_OK)
async def register_patron_endpoint(
    data: PatronCreate,
    conn: sqlite3.Connection = Depends(get_db),
) -> PatronRegisterResponse:
    """Register a new patron with deduplication.

    Deduplication is based on the patron name. If a patron with the same
    name already exists, the existing patron is returned with is_new=False.

    Args:
        data: Patron creation data with name and optional kind.
        conn: Database connection (injected via dependency).

    Returns:
        The created or existing patron with is_new flag.

    Raises:
        HTTPException: 500 if database operation fails.
    """
    # Check for existing patron by name (dedup)
    existing_result = find_patron_by_name(conn, data.name)

    if isinstance(existing_result, Failure):
        error = existing_result.failure()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check for existing patron: {error}",
        )

    existing = existing_result.unwrap()
    if existing is not None:
        # Return existing patron (return_existing semantics)
        return PatronRegisterResponse(
            id=existing.id,
            name=existing.name,
            kind=existing.kind,
            created_at=existing.created_at,
            is_new=False,
        )

    # Create new patron
    now = datetime.now(UTC)
    patron_id = PatronId(str(uuid.uuid4()))

    patron = Patron(
        id=patron_id,
        name=data.name,
        kind=data.kind,
        created_at=now,
    )

    result = create_patron(conn, patron)

    if isinstance(result, Failure):
        error = result.failure()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create patron: {error}",
        )

    created = result.unwrap()
    return PatronRegisterResponse(
        id=created.id,
        name=created.name,
        kind=created.kind,
        created_at=created.created_at,
        is_new=True,
    )


# =============================================================================
# GET /patrons/{patron_id} - Get a patron by ID
# =============================================================================


# @invar:allow entry_point_too_thick: FastAPI route with docstrings, type hints, and error handling
@router.get("/{patron_id}", response_model=Patron)
async def get_patron_endpoint(
    patron_id: str,
    conn: sqlite3.Connection = Depends(get_db),
) -> Patron:
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
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Patron not found: {patron_id}",
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get patron: {error}",
        )

    return result.unwrap()
