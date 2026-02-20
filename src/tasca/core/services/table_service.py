"""
Table service - core business logic for table operations.

This module provides pure functions for table version management and
optimistic concurrency. All functions are pure (no I/O) with contracts.
"""

from datetime import datetime

import deal

from tasca.core.domain.table import Table, TableId, TableStatus, TableUpdate, Version


# =============================================================================
# Errors
# =============================================================================


class VersionMismatchError(Exception):
    """Raised when expected_version does not match current version.

    This is the core domain error for optimistic concurrency conflicts.
    The shell layer will wrap this into a VersionConflictError for
    external consumers (API, etc.).

    Attributes:
        current_version: The actual version in the database.
        expected_version: The version the client expected.
    """

    # @invar:allow missing_contract: Exception class - contract on __init__ not meaningful
    def __init__(self, current_version: Version, expected_version: Version) -> None:
        self.current_version = current_version
        self.expected_version = expected_version
        super().__init__(
            f"Version conflict: expected {expected_version}, but current is {current_version}"
        )


# =============================================================================
# Version Validation
# =============================================================================


@deal.pre(lambda current_version, expected_version: current_version >= 1 and expected_version >= 1)
@deal.post(lambda result: isinstance(result, bool))
def validate_version_match(current_version: Version, expected_version: Version) -> bool:
    """Validate that expected version matches current version.

    This is the core optimistic concurrency check. The client must provide
    the version they last saw, and it must match the current version.

    Args:
        current_version: The actual version in the database.
        expected_version: The version the client expects.

    Returns:
        True if versions match, False otherwise.

    Doctests:
        >>> validate_version_match(Version(1), Version(1))
        True
        >>> validate_version_match(Version(5), Version(5))
        True
        >>> validate_version_match(Version(2), Version(1))
        False
        >>> validate_version_match(Version(1), Version(2))
        False
    """
    return current_version == expected_version


@deal.pre(lambda current_version: current_version >= 1)
@deal.post(lambda result: result > 1)
def increment_version(current_version: Version) -> Version:
    """Increment version for an update.

    Args:
        current_version: The current version before update.

    Returns:
        The new version number (current + 1).

    Doctests:
        >>> increment_version(Version(1))
        2
        >>> increment_version(Version(5))
        6
    """
    return Version(current_version + 1)


@deal.pre(lambda current_version, expected_version: current_version >= 1 and expected_version >= 1)
@deal.raises(VersionMismatchError)
def check_version_or_raise(current_version: Version, expected_version: Version) -> None:
    """Check version match and raise VersionMismatchError if mismatch.

    This is the assertive version of validate_version_match that raises
    on conflict instead of returning bool.

    Args:
        current_version: The actual version in the database.
        expected_version: The version the client expects.

    Raises:
        VersionMismatchError: If versions don't match.

    Doctests:
        >>> check_version_or_raise(Version(1), Version(1))  # No exception
        >>> check_version_or_raise(Version(2), Version(2))  # No exception
        >>> check_version_or_raise(Version(1), Version(2))  # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
            ...
        VersionMismatchError: Version conflict: expected 2, but current is 1
    """
    if not validate_version_match(current_version, expected_version):
        raise VersionMismatchError(current_version, expected_version)


# =============================================================================
# Replace-Only Update Logic
# =============================================================================


# @invar:allow partial_contract: Each @pre checks all parameters - deal expects separate @pre per condition
@deal.pre(lambda table, update, now: table is not None and update is not None and now is not None)
@deal.post(lambda result: result.version > 1)  # version always increments
@deal.post(lambda result: result.id is not None)  # id preserved
def prepare_table_update(table: Table, update: TableUpdate, now: datetime) -> Table:
    """Prepare an updated table with replace-only semantics.

    This is the CORE replace-only function. It creates a new Table with:
    - All fields from 'update' replacing existing values (no partial patch)
    - Version incremented by 1
    - updated_at set to 'now'
    - id and created_at preserved from original

    REPLACE-ONLY: The update must contain ALL user-modifiable fields.
    This is NOT a partial patch. Fields not in update will not be preserved
    from the original - the update replaces everything.

    Args:
        table: The current table state.
        update: The replacement data (must contain all fields).
        now: Current timestamp for updated_at.

    Returns:
        A new Table with replaced data and incremented version.

    Doctests:
        >>> from datetime import datetime
        >>> table = Table(
        ...     id=TableId("t1"),
        ...     question="Original",
        ...     context="Original context",
        ...     status=TableStatus.OPEN,
        ...     version=Version(1),
        ...     created_at=datetime(2024, 1, 1, 12, 0),
        ...     updated_at=datetime(2024, 1, 1, 12, 0)
        ... )
        >>> update = TableUpdate(
        ...     question="Updated",
        ...     context="New context",
        ...     status=TableStatus.OPEN
        ... )
        >>> result = prepare_table_update(table, update, datetime(2024, 1, 2, 12, 0))
        >>> result.question
        'Updated'
        >>> result.context
        'New context'
        >>> result.version
        2
        >>> result.id == table.id  # id preserved
        True
        >>> result.created_at == table.created_at  # created_at preserved
        True
    """
    new_version = increment_version(table.version)

    return Table(
        id=table.id,
        question=update.question,
        context=update.context,
        status=update.status,
        version=new_version,
        created_at=table.created_at,
        updated_at=now,
    )


# =============================================================================
# Version-aware Update with Validation
# =============================================================================


# @invar:allow partial_contract: Combined @pre checks all parameters in one lambda
@deal.pre(
    lambda table, update, expected_version, now: (
        table is not None and update is not None and expected_version >= 1 and now is not None
    )
)
@deal.raises(VersionMismatchError)
def prepare_versioned_update(
    table: Table,
    update: TableUpdate,
    expected_version: Version,
    now: datetime,
) -> Table:
    """Prepare an updated table with optimistic concurrency validation.

    This combines version checking with the replace-only update:
    1. Validates expected_version matches current version
    2. Creates updated table with incremented version

    Args:
        table: The current table state.
        update: The replacement data.
        expected_version: The version the client expects (for optimistic concurrency).
        now: Current timestamp for updated_at.

    Returns:
        A new Table with replaced data and incremented version.

    Raises:
        VersionMismatchError: If expected_version doesn't match current version.

    Doctests:
        >>> from datetime import datetime
        >>> table = Table(
        ...     id=TableId("t1"),
        ...     question="Original",
        ...     context=None,
        ...     status=TableStatus.OPEN,
        ...     version=Version(2),
        ...     created_at=datetime(2024, 1, 1, 12, 0),
        ...     updated_at=datetime(2024, 1, 1, 12, 0)
        ... )
        >>> update = TableUpdate(
        ...     question="Updated",
        ...     context="Added context",
        ...     status=TableStatus.OPEN
        ... )
        >>> # Correct version
        >>> result = prepare_versioned_update(table, update, Version(2), datetime(2024, 1, 2))
        >>> result.version
        3
        >>> result.question
        'Updated'
        >>> # Wrong version raises error
        >>> prepare_versioned_update(table, update, Version(1), datetime(2024, 1, 2))  # doctest: +IGNORE_EXCEPTION_DETAIL
        Traceback (most recent call last):
            ...
        VersionMismatchError: Version conflict: expected 1, but current is 2
    """
    # SPEC: Optimistic concurrency check - client must provide correct version
    check_version_or_raise(table.version, expected_version)

    # SPEC: Replace-only update - all fields from update, version incremented
    return prepare_table_update(table, update, now)
