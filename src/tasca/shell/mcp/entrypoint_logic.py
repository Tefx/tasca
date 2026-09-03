"""Pure MCP entrypoint helper logic.

This module holds deterministic payload shaping and validation helpers so
`entrypoints.py` stays focused on transport orchestration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import deal
from returns.result import Failure, Result, Success

from tasca.core.domain.patron import Patron
from tasca.core.domain.seat import Seat
from tasca.core.domain.table import Table, TableStatus, TableUpdate
from tasca.core.services.limits_service import LimitError
from tasca.shell.mcp.responses import error_response

EXIT_EMPTY_WAITS_THRESHOLD = 30
_NUDGE_THRESHOLD = 4
_URGENCY_THRESHOLD = 11
PatchApplyResult = tuple[TableUpdate, dict[str, Any] | None]

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
# @invar:allow entry_point_too_thick: MCP table payload has required compatibility aliases plus persisted metadata fields.
def build_table_dict(table: Table) -> dict[str, Any]:
    """Build MCP table payload."""
    identity_payload = _table_identity_payload(table)
    return {
        **identity_payload,
        "question": table.question,
        "title": table.question,
        "context": table.context,
        "status": table.status.value,
        "version": table.version,
        "created_at": table.created_at.isoformat(),
        "updated_at": table.updated_at.isoformat(),
        "host_ids": table.host_ids,
        "metadata": table.metadata,
        "policy": table.policy,
        "board": table.board,
    }


@deal.pre(lambda table: table is not None)
@deal.post(lambda result: "id" in result and "creator_id" in result)
def _table_identity_payload(table: Table) -> dict[str, Any]:
    """Build stable table identity aliases for MCP compatibility."""
    return {
        "id": table.id,
        "table_id": table.id,
        "creator_id": table.creator_patron_id,
        "created_by": table.creator_patron_id,
        "creator_patron_id": table.creator_patron_id,
    }


@deal.pre(lambda saying: saying is not None)
@deal.post(lambda result: "id" in result and "speaker" in result)
def format_saying_dict(saying: Any) -> dict[str, Any]:
    """Build MCP saying payload."""
    return {
        "id": saying.id,
        "table_id": saying.table_id,
        "sequence": saying.sequence,
        "speaker": saying.speaker.model_dump(mode="json"),
        "content": saying.content,
        "attachments": [
            attachment.model_dump(mode="json") for attachment in saying.attachments
        ],
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
def validate_speaker_constraints(
    speaker_kind: str,
    patron_id: str | None,
) -> dict[str, Any] | None:
    """Validate speaker kind/patron_id protocol constraints."""
    if speaker_kind == "agent" and patron_id is None:
        return _speaker_constraint_error("agent_missing_patron", speaker_kind, patron_id)
    if speaker_kind == "human" and patron_id is not None:
        return _speaker_constraint_error("human_with_patron", speaker_kind, patron_id)
    return None


@deal.pre(lambda kind, speaker_kind, patron_id: kind in {"agent_missing_patron", "human_with_patron"})
@deal.post(lambda result: result.get("ok") is False and "error" in result)
def _speaker_constraint_error(
    kind: str, speaker_kind: str, patron_id: str | None,
) -> dict[str, Any]:
    """Build speaker constraint error envelopes."""
    if kind == "agent_missing_patron":
        message = "patron_id is required when speaker_kind is 'agent'"
        details: dict[str, Any] = {"speaker_kind": speaker_kind}
    else:
        message = "patron_id must be null or omitted when speaker_kind is 'human'"
        details = {"speaker_kind": speaker_kind, "patron_id": patron_id}
    return error_response(
        "INVALID_REQUEST",
        message,
        details,
    )


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
            f"Table is now {new_status.value}. Call tasca.seat_heartbeat(state='done'), then report to the user."
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
def _parse_patch_status(
    current_table: Table, patch: dict[str, Any]
) -> Result[tuple[TableStatus, dict[str, Any] | None], str]:
    """Parse optional status patch into a TableStatus."""
    if "status" not in patch:
        return Success((current_table.status, None))
    status_value = patch["status"]
    try:
        return Success((TableStatus(status_value), None))
    except ValueError:
        return Success((current_table.status, error_response(
            "INVALID_STATUS",
            f"Invalid status value: {status_value}. Must be one of: open, paused, closed",
        )))


@deal.pre(lambda current_table, patch: current_table is not None and patch is not None)
@deal.post(lambda result: len(result) == 2)
# @invar:allow entry_point_too_thick: Patch adapter validates all documented table_update fields before a single storage update.
def apply_table_patch(current_table: Table, patch: dict[str, Any]) -> PatchApplyResult:
    """Apply supported table patch fields and validate status transitions."""
    new_status, status_error = _parse_patch_status(current_table, patch).unwrap()
    if status_error is not None:
        return _patch_error_result(current_table, status_error)
    new_question = patch.get("question", current_table.question)
    new_context = cast(str | None, patch.get("context", current_table.context))
    host_ids_result = _parse_patch_host_ids(patch, current_table.host_ids)
    if isinstance(host_ids_result, Failure):
        return _patch_error_result(
            current_table,
            host_ids_result.failure(),
        )
    new_host_ids = host_ids_result.unwrap()
    metadata_result = _parse_patch_json_object(patch, "metadata", current_table.metadata)
    if isinstance(metadata_result, Failure):
        return _patch_error_result(current_table, metadata_result.failure())
    policy_result = _parse_patch_json_object(patch, "policy", current_table.policy)
    if isinstance(policy_result, Failure):
        return _patch_error_result(current_table, policy_result.failure())
    board_result = _parse_patch_json_object(patch, "board", current_table.board)
    if isinstance(board_result, Failure):
        return _patch_error_result(current_table, board_result.failure())
    return _patch_success_result(
        new_question,
        new_context,
        new_status,
        new_host_ids,
        metadata_result.unwrap(),
        policy_result.unwrap(),
        board_result.unwrap(),
    )


def _parse_patch_host_ids(
    patch: dict[str, Any], current_host_ids: list[str]
) -> Result[list[str], dict[str, Any]]:
    """Parse and validate optional host_ids patch field."""
    host_ids = patch.get("host_ids", current_host_ids)
    if not isinstance(host_ids, list) or not all(isinstance(item, str) for item in host_ids):
        return Failure(error_response("INVALID_REQUEST", "host_ids must be a list of patron_id strings"))
    return Success(host_ids)


def _parse_patch_json_object(
    patch: dict[str, Any], key: str, current_value: dict[str, object]
) -> Result[dict[str, object], dict[str, Any]]:
    """Parse optional metadata/policy/board patch fields.

    Omitted keys preserve the current value. Explicit null clears to an empty
    object. Dict values replace the old value (last-writer-wins after version
    check).
    """
    if key not in patch:
        return Success(current_value)
    value = patch[key]
    if value is None:
        return Success({})
    if not isinstance(value, dict):
        return Failure(error_response("INVALID_REQUEST", f"{key} must be a JSON object or null"))
    return Success(cast(dict[str, object], value))


@deal.pre(lambda current_table, error: current_table is not None and error is not None)
@deal.post(lambda result: len(result) == 2 and result[1] is not None)
# @invar:allow entry_point_too_thick: Error result must preserve all replace-only table fields.
def _patch_error_result(
    current_table: Table,
    error: dict[str, Any],
) -> tuple[TableUpdate, dict[str, Any]]:
    """Build an unchanged update plus validation error."""
    return (
        TableUpdate(
            question=current_table.question,
            context=current_table.context,
            status=current_table.status,
            host_ids=current_table.host_ids,
            metadata=current_table.metadata,
            policy=current_table.policy,
            board=current_table.board,
        ),
        error,
    )


@deal.pre(
    lambda question, context, status, host_ids, metadata, policy, board: isinstance(host_ids, list)
    and isinstance(metadata, dict)
    and isinstance(policy, dict)
    and isinstance(board, dict)
)
@deal.post(lambda result: len(result) == 2 and result[1] is None)
# @invar:allow entry_point_too_thick: Success result enumerates all replace-only table fields explicitly.
def _patch_success_result(
    question: str,
    context: str | None,
    status: TableStatus,
    host_ids: list[str],
    metadata: dict[str, object],
    policy: dict[str, object],
    board: dict[str, object],
) -> tuple[TableUpdate, None]:
    """Build a validated table update tuple."""
    return TableUpdate(
        question=question,
        context=context,
        status=status,
        host_ids=host_ids,
        metadata=metadata,
        policy=policy,
        board=board,
    ), None


@deal.pre(lambda saying, mentions_all, mentions_resolved, mentions_unresolved: saying is not None)
@deal.post(lambda result: "id" in result and "_next_action" in result)
def build_say_response(
    saying: Any,
    mentions_all: bool,
    mentions_resolved: list[str],
    mentions_unresolved: list[str],
) -> dict[str, Any]:
    """Build table_say response payload."""
    compatibility = _say_compatibility_payload(saying)
    speaker = _saying_speaker_payload(saying)
    next_action = _say_next_action(saying.sequence)
    return _say_response_payload(
        saying, compatibility, speaker, next_action, mentions_all, mentions_resolved, mentions_unresolved
    ).unwrap()


def _say_response_payload(
    saying: Any,
    compatibility: dict[str, Any],
    speaker: dict[str, Any],
    next_action: str,
    mentions_all: bool,
    mentions_resolved: list[str],
    mentions_unresolved: list[str],
) -> Result[dict[str, Any], str]:
    """Build complete table_say response payload."""
    return Success({
        **compatibility,
        "mentions_all": mentions_all,
        "mentions_resolved": mentions_resolved,
        "mentions_unresolved": mentions_unresolved,
        "table_id": saying.table_id,
        "speaker": speaker,
        "content": saying.content,
        "attachments": [
            attachment.model_dump(mode="json") for attachment in saying.attachments
        ],
        "pinned": saying.pinned,
        "_next_action": next_action,
    })


@deal.pre(lambda saying: saying is not None)
@deal.post(lambda result: "saying_id" in result and "id" in result)
def _say_compatibility_payload(saying: Any) -> dict[str, Any]:
    """Build legacy and spec aliases for a table_say response."""
    return {
        "saying_id": saying.id,
        "sequence": saying.sequence,
        "created_at": saying.created_at.isoformat(),
        "id": saying.id,
    }


@deal.pre(lambda saying: saying is not None)
@deal.post(lambda result: "kind" in result and "name" in result)
def _saying_speaker_payload(saying: Any) -> dict[str, Any]:
    """Build speaker payload for saying responses."""
    return {
        "kind": saying.speaker.kind.value,
        "name": saying.speaker.name,
        "patron_id": saying.speaker.patron_id,
    }


@deal.pre(lambda sequence: sequence >= 0)
@deal.post(lambda result: "tasca.table_wait" in result)
def _say_next_action(sequence: int) -> str:
    """Build guidance after a successful table_say."""
    return (
        f"IMMEDIATELY call tasca.table_wait(since_sequence={sequence}). "
        "Your response = tool_call, not text."
    )


@deal.post(lambda result: isinstance(result, dict))
def build_table_say_compat_metadata(
    saying_type: str | None,
    reply_to_sequence: int | None,
) -> dict[str, Any]:
    """Build debug metadata for optional table_say compatibility fields."""
    metadata: dict[str, Any] = {}
    if saying_type is not None:
        metadata["saying_type"] = saying_type
    if reply_to_sequence is not None:
        metadata["reply_to_sequence"] = reply_to_sequence
    return metadata


@deal.post(lambda result: isinstance(result, dict) and "speaker_name" in result)
def build_table_update_actor_metadata(
    speaker_name: str,
    patron_id: str | None,
) -> dict[str, Any]:
    """Build debug metadata for table_update actor identity."""
    return {
        "speaker_name": speaker_name,
        "patron_id": patron_id,
    }


def _silence_nudge(empty_waits: int, threshold: int, next_sequence: int) -> Result[str, str]:
    return Success(
        f"No new messages ({empty_waits}/{threshold}). "
        f"IMMEDIATELY call tasca.table_wait(since_sequence={next_sequence}). "
        "Your response = tool_call, not text."
    )


def _silence_stall(empty_waits: int, next_sequence: int) -> Result[str, str]:
    return Success(
        f"Silence for {empty_waits} consecutive waits - discussion may be stalling. "
        "Consider whether the table's question has been fully addressed. "
        "If not, call tasca.table_say to advance: raise an unaddressed aspect, "
        "propose a synthesis, or ask a sharpening question. "
        f"If fully addressed, call tasca.table_wait(since_sequence={next_sequence}). "
        "Your response = tool_call, not text."
    )


def _silence_last_chance(empty_waits: int, threshold: int, next_sequence: int) -> Result[str, str]:
    return Success(
        f"Extended silence ({empty_waits}/{threshold}). If you have ANY remaining "
        "perspective on the table's question, call tasca.table_say NOW - "
        "this is your last chance before the discussion ends. "
        f"If the topic is genuinely exhausted, call tasca.table_wait(since_sequence={next_sequence}). "
        f"EXIT at {threshold} if nothing to add. "
        "Your response = tool_call, not text."
    )


def _silence_exit(threshold: int) -> Result[str, str]:
    return Success(
        f"Empty waits reached {threshold}. Discussion is over. "
        "IMMEDIATELY call tasca.seat_heartbeat(state='done'), then report to the user."
    )


@deal.pre(lambda empty_waits, next_sequence: empty_waits >= 0 and next_sequence >= -1)
@deal.post(lambda result: len(result) > 0)
def silence_next_action(empty_waits: int, next_sequence: int) -> str:
    """Build table_wait silence guidance text."""
    threshold = EXIT_EMPTY_WAITS_THRESHOLD
    if empty_waits < _NUDGE_THRESHOLD:
        return _silence_nudge(empty_waits, threshold, next_sequence).unwrap()
    if empty_waits < _URGENCY_THRESHOLD:
        return _silence_stall(empty_waits, next_sequence).unwrap()
    if empty_waits < threshold:
        return _silence_last_chance(empty_waits, threshold, next_sequence).unwrap()
    return _silence_exit(threshold).unwrap()


@deal.pre(lambda sayings, since_sequence: since_sequence >= -1)
@deal.post(lambda result: result >= -1)
def compute_next_sequence(sayings: list[Any], since_sequence: int) -> int:
    """Compute pagination cursor from returned sayings."""
    if sayings:
        return cast(int, max(s.sequence for s in sayings))
    return since_sequence
