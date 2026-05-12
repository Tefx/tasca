"""Transport-neutral patron registration orchestration.

This shell module centralizes patron registration invariants shared by REST
and MCP callers: explicit ``dedup_id`` idempotency, display-name compatibility
deduplication, ID selection, timestamping, and persistence.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from returns.result import Failure, Result, Success

from tasca.core.domain.patron import Patron, PatronId
from tasca.shell.storage.idempotency_repo import check_idempotency_key, store_idempotency_key
from tasca.shell.storage.patron_repo import (
    PatronError,
    create_patron,
    find_patron_by_name,
    get_patron,
)


@dataclass(frozen=True)
class PatronRegistrationOutcome:
    """Result payload for a transport-neutral patron registration."""

    patron: Patron
    is_new: bool


class PatronRegistrationError(Exception):
    """Base typed error for patron registration orchestration."""


class PatronLookupError(PatronRegistrationError):
    """Existing-patron dedup lookup failed."""

    def __init__(self, display_name: str, cause: PatronError) -> None:
        self.display_name = display_name
        self.cause = cause
        super().__init__(f"Failed to check for existing patron {display_name!r}: {cause}")


class PatronIdempotencyError(PatronRegistrationError):
    """Explicit dedup_id lookup/store failed."""

    def __init__(self, dedup_id: str, cause: object) -> None:
        self.dedup_id = dedup_id
        self.cause = cause
        super().__init__(f"Failed to process patron dedup_id {dedup_id!r}: {cause}")


class PatronCreateError(PatronRegistrationError):
    """Patron persistence failed after registration data was prepared."""

    def __init__(self, display_name: str, patron_id: PatronId, cause: PatronError) -> None:
        self.display_name = display_name
        self.patron_id = patron_id
        self.cause = cause
        super().__init__(f"Failed to create patron {display_name!r} ({patron_id}): {cause}")


# @shell_complexity: 5 branches for dedup lookup + return-existing + create + typed failure paths
def register_patron(
    conn: sqlite3.Connection,
    display_name: str,
    *,
    kind: str = "agent",
    alias: str | None = None,
    meta: dict[str, object] | None = None,
    patron_id: str | None = None,
    dedup_id: str | None = None,
    now: datetime | None = None,
) -> Result[PatronRegistrationOutcome, PatronRegistrationError]:
    """Register a patron with explicit dedup_id and display-name compatibility dedup.

    Args:
        conn: Database connection.
        display_name: Transport-resolved display name used as the dedup key.
        kind: Patron kind, usually ``agent`` or ``human``.
        alias: Optional short mention alias.
        meta: Optional caller metadata.
        patron_id: Optional caller-supplied patron identifier.
        dedup_id: Optional idempotency key. Reuse returns the original patron.
        now: Optional timestamp injection for deterministic tests/callers.

    Returns:
        ``Success(PatronRegistrationOutcome)`` or a typed orchestration failure.

    Example:
        >>> import sqlite3
        >>> from returns.result import Success
        >>> from tasca.shell.storage.database import apply_schema
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> first = register_patron(conn, "Agent Ada", patron_id="p-1", dedup_id="d-1")
        >>> isinstance(first, Success) and first.unwrap().is_new
        True
        >>> second = register_patron(conn, "Agent Ada", patron_id="p-2", dedup_id="d-1")
        >>> second.unwrap().patron.id == "p-1" and not second.unwrap().is_new
        True
        >>> third = register_patron(conn, "Agent Ada", patron_id="p-3")
        >>> third.unwrap().patron.id == "p-1" and not third.unwrap().is_new
        True
        >>> conn.close()
    """
    resource_key = "patron_register"
    created_at = now if now is not None else datetime.now(UTC)
    if dedup_id is not None:
        idempotency_result = check_idempotency_key(
            conn,
            resource_key,
            "patron_register",
            dedup_id,
            now=created_at,
        )
        if isinstance(idempotency_result, Failure):
            return Failure(PatronIdempotencyError(dedup_id, idempotency_result.failure()))
        cached = idempotency_result.unwrap()
        if cached is not None:
            cached_patron_id = cached.get("patron_id")
            if not isinstance(cached_patron_id, str):
                return Failure(PatronIdempotencyError(dedup_id, "cached response missing patron_id"))
            patron_result = get_patron(conn, PatronId(cached_patron_id))
            if isinstance(patron_result, Failure):
                return Failure(PatronLookupError(display_name, patron_result.failure()))
            return Success(PatronRegistrationOutcome(patron=patron_result.unwrap(), is_new=False))

    existing_result = find_patron_by_name(conn, display_name)
    if isinstance(existing_result, Failure):
        return Failure(PatronLookupError(display_name, existing_result.failure()))

    existing = existing_result.unwrap()
    if existing is not None:
        if dedup_id is not None:
            store_result = store_idempotency_key(
                conn,
                resource_key,
                "patron_register",
                dedup_id,
                {"patron_id": existing.id},
                now=created_at,
            )
            if isinstance(store_result, Failure):
                return Failure(PatronIdempotencyError(dedup_id, store_result.failure()))
        return Success(PatronRegistrationOutcome(patron=existing, is_new=False))

    selected_patron_id = PatronId(patron_id) if patron_id is not None else PatronId(str(uuid.uuid4()))
    patron = Patron(
        id=selected_patron_id,
        name=display_name,
        kind=kind,
        alias=alias,
        meta=meta,
        created_at=created_at,
    )
    create_result = create_patron(conn, patron)
    if isinstance(create_result, Failure):
        return Failure(PatronCreateError(display_name, selected_patron_id, create_result.failure()))

    created = create_result.unwrap()
    if dedup_id is not None:
        store_result = store_idempotency_key(
            conn,
            resource_key,
            "patron_register",
            dedup_id,
            {"patron_id": created.id},
            now=created_at,
        )
        if isinstance(store_result, Failure):
            return Failure(PatronIdempotencyError(dedup_id, store_result.failure()))

    return Success(PatronRegistrationOutcome(patron=created, is_new=True))
