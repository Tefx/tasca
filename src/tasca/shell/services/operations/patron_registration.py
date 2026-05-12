"""Transport-neutral patron registration orchestration.

This shell module centralizes patron registration invariants shared by REST
and MCP callers: deduplication by display name, ID selection, timestamping,
and persistence. Transport adapters remain responsible for idempotency caches
and response/error envelope shaping.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from returns.result import Failure, Result, Success

from tasca.core.domain.patron import Patron, PatronId
from tasca.shell.storage.patron_repo import PatronError, create_patron, find_patron_by_name


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


class PatronCreateError(PatronRegistrationError):
    """Patron persistence failed after registration data was prepared."""

    def __init__(self, display_name: str, patron_id: PatronId, cause: PatronError) -> None:
        self.display_name = display_name
        self.patron_id = patron_id
        self.cause = cause
        super().__init__(f"Failed to create patron {display_name!r} ({patron_id}): {cause}")


# @invar:allow dead_export: extraction step publishes shared op before transport rewiring
# @shell_complexity: 5 branches for dedup lookup + return-existing + create + typed failure paths
def register_patron(
    conn: sqlite3.Connection,
    display_name: str,
    *,
    kind: str = "agent",
    alias: str | None = None,
    meta: dict[str, object] | None = None,
    patron_id: str | None = None,
    now: datetime | None = None,
) -> Result[PatronRegistrationOutcome, PatronRegistrationError]:
    """Register a patron, returning an existing name match when present.

    Args:
        conn: Database connection.
        display_name: Transport-resolved display name used as the dedup key.
        kind: Patron kind, usually ``agent`` or ``human``.
        alias: Optional short mention alias.
        meta: Optional caller metadata.
        patron_id: Optional caller-supplied patron identifier.
        now: Optional timestamp injection for deterministic tests/callers.

    Returns:
        ``Success(PatronRegistrationOutcome)`` or a typed orchestration failure.

    Example:
        >>> import sqlite3
        >>> from returns.result import Success
        >>> from tasca.shell.storage.database import apply_schema
        >>> conn = sqlite3.connect(":memory:")
        >>> _ = apply_schema(conn)
        >>> first = register_patron(conn, "Agent Ada", patron_id="p-1")
        >>> isinstance(first, Success) and first.unwrap().is_new
        True
        >>> second = register_patron(conn, "Agent Ada")
        >>> second.unwrap().patron.id == "p-1" and not second.unwrap().is_new
        True
        >>> conn.close()
    """
    existing_result = find_patron_by_name(conn, display_name)
    if isinstance(existing_result, Failure):
        return Failure(PatronLookupError(display_name, existing_result.failure()))

    existing = existing_result.unwrap()
    if existing is not None:
        return Success(PatronRegistrationOutcome(patron=existing, is_new=False))

    created_at = now if now is not None else datetime.now(UTC)
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

    return Success(PatronRegistrationOutcome(patron=create_result.unwrap(), is_new=True))
