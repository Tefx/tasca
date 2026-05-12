"""Transport-neutral table control operation.

The operation in this module is the shell-application owner for table lifecycle
control.  It validates and normalizes the requested action, builds canonical
CONTROL saying content, and delegates the all-or-nothing mutation to
``atomic_control_table``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from returns.result import Failure, Result, Success

from tasca.core.domain.saying import Saying, Speaker
from tasca.core.domain.table import Table, TableId, TableStatus, Version
from tasca.core.table_state_machine import (
    can_transition_to_closed,
    can_transition_to_open,
    can_transition_to_paused,
    transition_to_closed,
    transition_to_open,
    transition_to_paused,
)
from tasca.shell.storage.control_repo import (
    ControlError,
    ControlVersionConflictError,
    atomic_control_table,
)
from tasca.shell.storage.table_repo import TableError, TableNotFoundError, get_table


class TableControlAction(StrEnum):
    """Supported table control actions."""

    PAUSE = "pause"
    RESUME = "resume"
    CLOSE = "close"


class TableControlErrorCode(StrEnum):
    """Transport-neutral table control failure codes."""

    INVALID_ACTION = "InvalidAction"
    TABLE_NOT_FOUND = "TableNotFound"
    INVALID_TRANSITION = "InvalidTransition"
    VERSION_CONFLICT = "VersionConflict"
    STORAGE_ERROR = "StorageError"


@dataclass(frozen=True)
class TableControlOutcome:
    """Successful table control result shared by transports."""

    table: Table
    control_saying: Saying
    action: TableControlAction
    control_content: str


@dataclass(frozen=True)
class TableControlOperationError:
    """Transport-neutral, typed failure for table control."""

    code: TableControlErrorCode
    message: str
    table_id: str
    action: str | None = None
    current_status: TableStatus | None = None
    expected_version: Version | None = None
    actual_version: Version | None = None
    storage_error: str | None = None


def normalize_control_action(action: str) -> Result[TableControlAction, TableControlOperationError]:
    """Normalize a raw action string into a table control action.

    >>> normalize_control_action(" PAUSE ").unwrap()
    <TableControlAction.PAUSE: 'pause'>
    >>> normalize_control_action("archive").failure().code
    <TableControlErrorCode.INVALID_ACTION: 'InvalidAction'>
    """
    normalized = action.strip().lower()
    try:
        return Success(TableControlAction(normalized))
    except ValueError:
        return Failure(
            TableControlOperationError(
                code=TableControlErrorCode.INVALID_ACTION,
                message="Invalid control action. Must be one of: pause, resume, close.",
                table_id="",
                action=action,
            )
        )


# @invar:allow shell_result: Pure canonical formatting helper, not an I/O shell boundary.
# @shell_orchestration: Canonical audit content is owned with the table.control shell operation.
def build_control_content(action: TableControlAction, reason: str | None) -> str:
    """Build canonical CONTROL saying content.

    >>> build_control_content(TableControlAction.PAUSE, None)
    '**CONTROL: PAUSE**'
    >>> build_control_content(TableControlAction.CLOSE, 'Done').splitlines()
    ['**CONTROL: CLOSE**', '', 'Reason: Done']
    """
    content = f"**CONTROL: {action.value.upper()}**"
    normalized_reason = reason.strip() if reason is not None else ""
    if normalized_reason:
        content = f"{content}\n\nReason: {normalized_reason}"
    return content


def _transition_for_action(
    action: TableControlAction,
    current_status: TableStatus,
) -> Result[TableStatus, TableControlOperationError]:
    transition_rules = {
        TableControlAction.PAUSE: (can_transition_to_paused, transition_to_paused),
        TableControlAction.RESUME: (can_transition_to_open, transition_to_open),
        TableControlAction.CLOSE: (can_transition_to_closed, transition_to_closed),
    }
    can_transition, transition = transition_rules[action]
    if not can_transition(current_status):
        return Failure(
            TableControlOperationError(
                code=TableControlErrorCode.INVALID_TRANSITION,
                message=(f"Cannot {action.value} table with status '{current_status.value}'."),
                table_id="",
                action=action.value,
                current_status=current_status,
            )
        )
    return Success(transition(current_status))


# @invar:allow shell_result: Pure error mapper returns typed operation error, not a shell boundary.
# @shell_orchestration: Maps storage failures into operation-owned typed errors for transports.
def _storage_error(
    table_id: str,
    action: TableControlAction,
    error: TableError | ControlError,
) -> TableControlOperationError:
    if isinstance(error, TableNotFoundError):
        return TableControlOperationError(
            code=TableControlErrorCode.TABLE_NOT_FOUND,
            message=f"Table not found: {table_id}",
            table_id=table_id,
            action=action.value,
        )
    if isinstance(error, ControlVersionConflictError):
        return TableControlOperationError(
            code=TableControlErrorCode.VERSION_CONFLICT,
            message="Table version conflict during control operation.",
            table_id=table_id,
            action=action.value,
            expected_version=error.expected_version,
            actual_version=error.actual_version,
            current_status=error.actual_status,
            storage_error=str(error),
        )
    return TableControlOperationError(
        code=TableControlErrorCode.STORAGE_ERROR,
        message="Failed to execute table control operation.",
        table_id=table_id,
        action=action.value,
        storage_error=str(error),
    )


# @shell_complexity: This is the shared application transaction boundary adapter for table.control.
# @invar:allow dead_export: Shared operation is intentionally introduced before transport adoption.
def execute_table_control(
    conn: sqlite3.Connection,
    table_id: str,
    action: str,
    speaker: Speaker,
    reason: str | None,
    now: datetime | None = None,
) -> Result[TableControlOutcome, TableControlOperationError]:
    """Validate and execute a table control operation.

    The current table is read before state validation. If the table changes
    after this read, ``atomic_control_table`` reports a version conflict rather
    than reclassifying the request as a new-state invalid transition; this keeps
    stale-definition vs new-state precedence explicit and lossless.
    """
    action_result = normalize_control_action(action)
    if isinstance(action_result, Failure):
        error = action_result.failure()
        return Failure(
            TableControlOperationError(
                code=error.code,
                message=error.message,
                table_id=table_id,
                action=error.action,
            )
        )
    control_action = action_result.unwrap()

    current_result = get_table(conn, TableId(table_id))
    if isinstance(current_result, Failure):
        return Failure(_storage_error(table_id, control_action, current_result.failure()))
    current_table = current_result.unwrap()

    transition_result = _transition_for_action(control_action, current_table.status)
    if isinstance(transition_result, Failure):
        error = transition_result.failure()
        return Failure(
            TableControlOperationError(
                code=error.code,
                message=error.message,
                table_id=table_id,
                action=control_action.value,
                current_status=error.current_status,
            )
        )
    new_status = transition_result.unwrap()

    operation_time = now if now is not None else datetime.now(UTC)
    control_content = build_control_content(control_action, reason)
    control_result = atomic_control_table(
        conn=conn,
        table_id=table_id,
        speaker=speaker,
        control_content=control_content,
        new_status=new_status,
        current_table=current_table,
        now=operation_time,
    )
    if isinstance(control_result, Failure):
        return Failure(_storage_error(table_id, control_action, control_result.failure()))

    control_saying, updated_table = control_result.unwrap()
    return Success(
        TableControlOutcome(
            table=updated_table,
            control_saying=control_saying,
            action=control_action,
            control_content=control_content,
        )
    )
