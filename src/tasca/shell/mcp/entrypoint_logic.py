"""Pure MCP entrypoint helper logic.

This module holds deterministic payload shaping and validation helpers so
`entrypoints.py` stays focused on transport orchestration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import deal

from tasca.core.domain.patron import Patron
from tasca.core.domain.seat import Seat
from tasca.core.domain.table import Table, TableStatus, TableUpdate
from tasca.core.services.limits_service import LimitError
from tasca.core.table_state_machine import (
    can_transition_to_closed,
    can_transition_to_open,
    can_transition_to_paused,
    transition_to_closed,
    transition_to_open,
    transition_to_paused,
)
from tasca.shell.mcp.responses import error_response

EXIT_EMPTY_WAITS_THRESHOLD = 30
_NUDGE_THRESHOLD = 4
_URGENCY_THRESHOLD = 11

_JOIN_OPENING_ACTION = (
    "The table has a question but no discussion yet. "
    "IMMEDIATELY call tasca.table_say to share your opening perspective "
    "on the table's question. You are starting the discussion. "
    "After speaking, call tasca.table_wait(since_sequence=...). "
    "Your response must be a tool_call - not text."
)

_JOIN_WAIT_ACTION_TEMPLATE = (
    "IMMEDIATELY call tasca.table_wait(since_sequence={seq}). "
    "Your response must be a tool_call - not text. "
    "Loop: wait -> think -> say/skip -> heartbeat -> wait. "
    "Exit only when: human says stop, consensus, table closed, "
    "or consecutive_empty_waits=30 with nothing to add."
)


@deal.pre(lambda error: error is not None)
@deal.post(lambda result: result.get("ok") is False and "error" in result)
def limit_error_to_response(error: LimitError) -> dict[str, Any]:
    """Map limit service errors to MCP error envelope."""
    return error_response(
        "LIMIT_EXCEEDED",
        error.message,
        {
            "limit_kind": error.kind.value,
            "limit": error.limit,
            "actual": error.actual,
        },
    )


@deal.pre(lambda patron, is_new: patron is not None)
@deal.post(lambda result: "patron_id" in result and "display_name" in result)
def build_patron_response_data(patron: Patron, *, is_new: bool) -> dict[str, Any]:
    """Build MCP patron response with spec and backward-compatible fields."""
    return {
        "patron_id": patron.id,
        "display_name": patron.name,
        "alias": patron.alias,
        "server_ts": patron.created_at.isoformat(),
        "is_new": is_new,
        "id": patron.id,
        "name": patron.name,
        "kind": patron.kind,
        "created_at": patron.created_at.isoformat(),
        "meta": patron.meta,
    }


@deal.pre(lambda table: table is not None)
@deal.post(lambda result: "id" in result and "status" in result)
def build_table_dict(table: Table) -> dict[str, Any]:
    """Build MCP table payload."""
    return {
        "id": table.id,
        "question": table.question,
        "context": table.context,
        "status": table.status.value,
        "version": table.version,
        "created_at": table.created_at.isoformat(),
        "updated_at": table.updated_at.isoformat(),
    }


@deal.pre(lambda saying: saying is not None)
@deal.post(lambda result: "id" in result and "speaker" in result)
def format_saying_dict(saying: Any) -> dict[str, Any]:
    """Build MCP saying payload."""
    return {
        "id": saying.id,
        "table_id": saying.table_id,
        "sequence": saying.sequence,
        "speaker": {
            "kind": saying.speaker.kind.value,
            "name": saying.speaker.name,
            "patron_id": saying.speaker.patron_id,
        },
        "content": saying.content,
        "pinned": saying.pinned,
        "created_at": saying.created_at.isoformat(),
    }


@deal.pre(lambda seat, expires_at: seat is not None and isinstance(expires_at, datetime))
@deal.post(lambda result: "id" in result and "expires_at" in result)
def build_seat_dict(seat: Seat, expires_at: datetime) -> dict[str, Any]:
    """Build MCP seat payload."""
    return {
        "id": seat.id,
        "table_id": seat.table_id,
        "patron_id": seat.patron_id,
        "state": seat.state.value,
        "last_heartbeat": seat.last_heartbeat.isoformat(),
        "joined_at": seat.joined_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


@deal.post(lambda result: result is None or (result.get("ok") is False and "error" in result))
# @invar:allow entry_point_too_thick: Branching validates MCP protocol invariants and keeps
#   explicit error payload shape for agent/human speaker constraints.
def validate_speaker_constraints(
    speaker_kind: str,
    patron_id: str | None,
) -> dict[str, Any] | None:
    """Validate speaker kind/patron_id protocol constraints."""
    if speaker_kind == "agent" and patron_id is None:
        return error_response(
            "INVALID_REQUEST",
            "patron_id is required when speaker_kind is 'agent'",
            {"speaker_kind": speaker_kind},
        )
    if speaker_kind == "human" and patron_id is not None:
        return error_response(
            "INVALID_REQUEST",
            "patron_id must be null or omitted when speaker_kind is 'human'",
            {"speaker_kind": speaker_kind, "patron_id": patron_id},
        )
    return None


@deal.pre(lambda action, current_status: action is not None and current_status is not None)
@deal.post(lambda result: len(result) == 2)
# @invar:allow entry_point_too_thick: Transition matrix plus error shaping must stay explicit
#   to preserve protocol-compatible OPERATION_NOT_ALLOWED messages.
def validate_control_action(
    action: str,
    current_status: TableStatus,
) -> tuple[TableStatus | None, dict[str, Any] | None]:
    """Validate table control transition and derive next status."""
    transitions = {
        "pause": (
            can_transition_to_paused,
            transition_to_paused,
            "Only OPEN tables can be paused.",
        ),
        "resume": (
            can_transition_to_open,
            transition_to_open,
            "Only PAUSED tables can be resumed.",
        ),
        "close": (can_transition_to_closed, transition_to_closed, ""),
    }
    if action not in transitions:
        return None, error_response("INVALID_ACTION", f"Unknown action: {action}")
    can_transition, transition_fn, suffix = transitions[action]
    if not can_transition(current_status):
        reason = f" Cannot {action} table with status '{current_status.value}'."
        message = reason[1:] if not suffix else f"{reason[1:]} {suffix}"
        return None, error_response(
            "OPERATION_NOT_ALLOWED", message, {"table_status": current_status.value}
        )
    return transition_fn(current_status), None


@deal.pre(lambda new_status, control_sequence: control_sequence >= 0)
@deal.post(lambda result: "table_status" in result and "_next_action" in result)
def build_control_response(new_status: TableStatus, control_sequence: int) -> dict[str, Any]:
    """Build table_control success payload."""
    if new_status == TableStatus.OPEN:
        next_action = (
            "Table resumed. IMMEDIATELY call tasca.table_wait. Your response = tool_call, not text."
        )
    else:
        next_action = (
            f"Table is now {new_status.value}. Call tasca.table_leave, then report to the user."
        )
    return {
        "table_status": new_status.value,
        "control_saying_sequence": control_sequence,
        "_next_action": next_action,
    }


@deal.pre(lambda has_history, next_sequence: next_sequence >= -1)
@deal.post(lambda result: len(result) > 0)
def build_join_next_action(has_history: bool, next_sequence: int) -> str:
    """Build table_join next-action guidance text."""
    if not has_history:
        return _JOIN_OPENING_ACTION
    return _JOIN_WAIT_ACTION_TEMPLATE.format(seq=next_sequence)


# +#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+#+
# @invar:allow shell_result: Pure patch parser returns TableStatus/error tuple consumed by
#   shell adapters; not an I/O boundary and intentionally not a Result envelope.
def _parse_patch_status(
    current_table: Table, patch: dict[str, Any]
) -> tuple[TableStatus, dict[str, Any] | None]:
    """Parse optional status patch into a TableStatus."""
    if "status" not in patch:
        return current_table.status, None
    status_value = patch["status"]
    try:
        return TableStatus(status_value), None
    except ValueError:
        return current_table.status, error_response(
            "INVALID_STATUS",
            f"Invalid status value: {status_value}. Must be one of: open, paused, closed",
        )


@deal.pre(lambda current_table, patch: current_table is not None and patch is not None)
@deal.post(lambda result: len(result) == 2)
# @invar:allow entry_point_too_thick: Patch assembly carries backward-compatible fallback
#   semantics for invalid status while preserving existing MCP response shape.
def apply_table_patch(
    current_table: Table,
    patch: dict[str, Any],
) -> tuple[TableUpdate, dict[str, Any] | None]:
    """Apply supported table patch fields and validate status transitions."""
    new_status, status_error = _parse_patch_status(current_table, patch)
    if status_error is not None:
        return (
            TableUpdate(
                question=current_table.question,
                context=current_table.context,
                status=current_table.status,
            ),
            status_error,
        )
    new_question = patch.get("question", current_table.question)
    new_context = patch.get("context", current_table.context)
    return TableUpdate(
        question=new_question,
        context=new_context,
        status=new_status,
    ), None


@deal.pre(lambda saying, mentions_all, mentions_resolved, mentions_unresolved: saying is not None)
@deal.post(lambda result: "id" in result and "_next_action" in result)
# @invar:allow entry_point_too_thick: Response payload keeps spec and backward-compatible
#   fields in one place to prevent drift across table_say call sites.
def build_say_response(
    saying: Any,
    mentions_all: bool,
    mentions_resolved: list[str],
    mentions_unresolved: list[str],
) -> dict[str, Any]:
    """Build table_say response payload."""
    speaker_payload = {
        "kind": saying.speaker.kind.value,
        "name": saying.speaker.name,
        "patron_id": saying.speaker.patron_id,
    }
    next_action = (
        f"IMMEDIATELY call tasca.table_wait(since_sequence={saying.sequence}). "
        "Your response = tool_call, not text."
    )
    return {
        "saying_id": saying.id,
        "sequence": saying.sequence,
        "created_at": saying.created_at.isoformat(),
        "mentions_all": mentions_all,
        "mentions_resolved": mentions_resolved,
        "mentions_unresolved": mentions_unresolved,
        "id": saying.id,
        "table_id": saying.table_id,
        "speaker": speaker_payload,
        "content": saying.content,
        "pinned": saying.pinned,
        "_next_action": next_action,
    }


# @invar:allow shell_result: Helper builds MCP next-action guidance string for wait loop.
def _silence_nudge(empty_waits: int, threshold: int, next_sequence: int) -> str:
    return (
        f"No new messages ({empty_waits}/{threshold}). "
        f"IMMEDIATELY call tasca.table_wait(since_sequence={next_sequence}). "
        "Your response = tool_call, not text."
    )


# @invar:allow shell_result: Helper builds MCP next-action guidance string for wait loop.
def _silence_stall(empty_waits: int, next_sequence: int) -> str:
    return (
        f"Silence for {empty_waits} consecutive waits - discussion may be stalling. "
        "Consider whether the table's question has been fully addressed. "
        "If not, call tasca.table_say to advance: raise an unaddressed aspect, "
        "propose a synthesis, or ask a sharpening question. "
        f"If fully addressed, call tasca.table_wait(since_sequence={next_sequence}). "
        "Your response = tool_call, not text."
    )


# @invar:allow shell_result: Helper builds MCP next-action guidance string for wait loop.
def _silence_last_chance(empty_waits: int, threshold: int, next_sequence: int) -> str:
    return (
        f"Extended silence ({empty_waits}/{threshold}). If you have ANY remaining "
        "perspective on the table's question, call tasca.table_say NOW - "
        "this is your last chance before the discussion ends. "
        f"If the topic is genuinely exhausted, call tasca.table_wait(since_sequence={next_sequence}). "
        f"EXIT at {threshold} if nothing to add. "
        "Your response = tool_call, not text."
    )


# @invar:allow shell_result: Helper builds MCP next-action guidance string for wait loop.
def _silence_exit(threshold: int) -> str:
    return (
        f"Empty waits reached {threshold}. Discussion is over. "
        "IMMEDIATELY call tasca.table_leave, then report to the user."
    )


@deal.pre(lambda empty_waits, next_sequence: empty_waits >= 0 and next_sequence >= -1)
@deal.post(lambda result: len(result) > 0)
def silence_next_action(empty_waits: int, next_sequence: int) -> str:
    """Build table_wait silence guidance text."""
    threshold = EXIT_EMPTY_WAITS_THRESHOLD
    if empty_waits < _NUDGE_THRESHOLD:
        return _silence_nudge(empty_waits, threshold, next_sequence)
    if empty_waits < _URGENCY_THRESHOLD:
        return _silence_stall(empty_waits, next_sequence)
    if empty_waits < threshold:
        return _silence_last_chance(empty_waits, threshold, next_sequence)
    return _silence_exit(threshold)


@deal.pre(lambda sayings, since_sequence: since_sequence >= -1)
@deal.post(lambda result: result >= -1)
def compute_next_sequence(sayings: list[Any], since_sequence: int) -> int:
    """Compute pagination cursor from returned sayings."""
    if sayings:
        return max(s.sequence for s in sayings)
    return since_sequence
