"""
Atomic control operations repository.

This module provides atomic database operations that span multiple tables
(sayings + tables) to ensure consistency for control actions.

All operations use explicit transactions with BEGIN IMMEDIATE to guarantee
atomicity - either both the saying append and status update succeed, or both
are rolled back.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from returns.result import Failure, Result, Success

from tasca.core.domain.saying import Saying, SayingId, Speaker
from tasca.core.domain.table import Table, TableId, TableStatus, TableUpdate, Version
from tasca.core.services.saying_service import compute_next_sequence
from tasca.core.services.table_service import VersionMismatchError
from tasca.shell.storage.table_repo import TableNotFoundError, VersionConflictError


# =============================================================================
# Error Types
# =============================================================================


class ControlError(Exception):
    """Base error for control operations."""

    pass


class ControlVersionConflictError(ControlError):
    """Version conflict during atomic control operation.

    This error is raised when the optimistic concurrency check fails
    during a control operation (pause/resume/close).

    Attributes:
        table_id: The ID of the table that had the conflict.
        expected_version: The version the client expected.
    """

    def __init__(self, table_id: str, expected_version: int) -> None:
        self.table_id = table_id
        self.expected_version = expected_version
        super().__init__(
            f"Version conflict for table {table_id}: expected {expected_version}, but was modified"
        )

    def to_json(self) -> dict:
        """Convert error to JSON for API responses.

        Returns:
            JSON representation of the conflict error.
        """
        return {
            "error": "version_conflict",
            "table_id": self.table_id,
            "expected_version": self.expected_version,
            "message": str(self),
        }


class ControlIntegrityError(ControlError):
    """Integrity constraint violation during control operation.

    Raised when a unique constraint is violated, such as a duplicate
    sequence number.
    """

    pass


class ControlDatabaseError(ControlError):
    """Database error during control operation."""

    pass


# @shell_orchestration: Multi-step atomic operation (BEGIN IMMEDIATE, insert saying, update table, COMMIT)
# @invar:allow function_size: Transaction boundary - atomicity requires all operations in one function
# @shell_complexity: transaction boundary requires lock/insert/update/rollback branches to preserve atomic audit+state semantics.
def atomic_control_table(
    conn: sqlite3.Connection,
    table_id: str,
    speaker: Speaker,
    control_content: str,
    new_status: TableStatus,
    current_table: Table,
    now: datetime,
) -> Result[tuple[Saying, Table], ControlError]:
    """Atomically append CONTROL saying and update table status.

    This operation is atomic:
    1. Begin transaction with BEGIN IMMEDIATE (acquires write lock)
    2. Get current max sequence and insert CONTROL saying
    3. Update table status with version increment
    4. Commit (or rollback on any error)

    If any step fails, the entire operation is rolled back, ensuring
    the audit trail (CONTROL saying) and state change (status) remain
    consistent.

    Args:
        conn: Database connection (must have transaction support).
        table_id: UUID of the table.
        speaker: Speaker information for the CONTROL saying.
        control_content: Content of the CONTROL saying.
        new_status: The new status to set on the table.
        current_table: Current table state (for optimistic concurrency).
        now: Current timestamp for created_at and updated_at.

    Returns:
        Success with (Saying, Table) tuple, or Failure with ControlError.

    Note:
        The caller is responsible for validating that the state transition
        is allowed (e.g., can pause from current state). This function
        performs the atomic database operations only.
    """
    cursor = conn.cursor()

    try:
        # Step 1: Acquire write lock and start transaction
        cursor.execute("BEGIN IMMEDIATE")

        try:
            # Step 2: Append CONTROL saying (within transaction)
            saying_id = SayingId(str(uuid.uuid4()))

            # Get current max sequence for this table
            cursor.execute(
                "SELECT COALESCE(MAX(sequence), -1) FROM sayings WHERE table_id = ?",
                (table_id,),
            )
            row = cursor.fetchone()
            current_max = int(row[0]) if row else -1

            # Compute next sequence (pure function from core)
            next_sequence = compute_next_sequence(current_max)

            # Insert the saying
            cursor.execute(
                """
                INSERT INTO sayings (
                    id, table_id, sequence, speaker_kind, speaker_name,
                    patron_id, content, pinned, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    saying_id,
                    table_id,
                    next_sequence,
                    speaker.kind.value,
                    speaker.name,
                    speaker.patron_id,
                    control_content,
                    0,  # pinned defaults to False
                    now.isoformat(),
                ),
            )

            # Step 3: Update table status with optimistic concurrency check
            update = TableUpdate(
                question=current_table.question,
                context=current_table.context,
                status=new_status,
            )

            # Compute new version
            new_version = Version(current_table.version + 1)

            # Atomic update with version check in WHERE clause
            cursor.execute(
                """
                UPDATE tables
                SET question = ?, context = ?, status = ?, version = ?, updated_at = ?
                WHERE id = ? AND version = ?
                """,
                (
                    update.question,
                    update.context,
                    update.status.value,
                    new_version,
                    now.isoformat(),
                    table_id,
                    current_table.version,  # Optimistic concurrency check
                ),
            )

            # Check if update succeeded (version matched)
            if cursor.rowcount == 0:
                # Version mismatch - concurrent modification
                conn.rollback()
                return Failure(ControlVersionConflictError(table_id, current_table.version))

            # Step 4: Commit the transaction
            conn.commit()

            # Build return objects
            saying = Saying(
                id=saying_id,
                table_id=table_id,
                sequence=next_sequence,
                speaker=speaker,
                content=control_content,
                pinned=False,
                created_at=now,
            )

            updated_table = Table(
                id=current_table.id,
                question=current_table.question,
                context=current_table.context,
                status=new_status,
                version=new_version,
                created_at=current_table.created_at,
                updated_at=now,
                creator_patron_id=current_table.creator_patron_id,
            )

            return Success((saying, updated_table))

        except sqlite3.IntegrityError as e:
            conn.rollback()
            return Failure(ControlIntegrityError(f"Integrity error: {e}"))

        except Exception:
            # Catch-all: ensure rollback for ANY non-sqlite exception after BEGIN
            # Re-raise to avoid swallowing unexpected errors (bugs should surface)
            conn.rollback()
            raise

    except sqlite3.Error as e:
        # Ensure rollback on any outer error (e.g., BEGIN IMMEDIATE failure)
        conn.rollback()
        return Failure(ControlDatabaseError(f"Database error: {e}"))
