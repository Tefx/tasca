"""Declarative MCP tool surface contracts.

This module is intentionally runtime-neutral: it names the public MCP tools and
records their parameter defaults, parameter documentation, and source anchors for
server registration. Runtime handlers and transport behavior remain in
``server.py``/``entrypoints.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, cast

from pydantic import Field
from pydantic.fields import FieldInfo


@dataclass(frozen=True, slots=True)
class ParameterContract:
    """Declarative metadata for one MCP tool parameter."""

    name: str
    description: str
    required: bool
    default: object | None = None


@dataclass(frozen=True, slots=True)
class ToolContract:
    """Declarative metadata for one public MCP tool."""

    tool_name: str
    spec_name: str
    summary: str
    description: str
    parameters: tuple[ParameterContract, ...]
    spec_anchor: str
    runtime_entrypoint: str
    surface: str = "mcp_server_registration"


SPEC_DOC: Final = "docs/tasca-mcp-interface-v0.1.md"
TOOLS_SPEC_ANCHOR: Final = "tasca-mcp-interface-spec-v0-1/5-tools"
DEFAULTS_SPEC_ANCHOR: Final = (
    "tasca-mcp-interface-spec-v0-1/1-2-defaults-limits-and-error-format-normative/"
    "recommended-defaults-v0-1"
)
ERROR_SHAPE_SPEC_ANCHOR: Final = (
    "tasca-mcp-interface-spec-v0-1/1-2-defaults-limits-and-error-format-normative/"
    "error-response-shape"
)

RECOMMENDED_DEFAULTS: Final[dict[str, int]] = {
    "table.join.history_limit": 10,
    "table.join.history_max_bytes": 65536,
    "table.listen.limit": 50,
    "table.wait.limit": 50,
    "table.wait.wait_ms": 10000,
    "seat.heartbeat.ttl_ms": 60000,
    "saying.content.max_bytes": 65536,
}

SPEC_PARAMETER_DEFAULTS: Final[dict[str, object]] = {
    "table_say.saying_type": "text",
    "seat_heartbeat.state": "running",
    "seat_heartbeat.ttl_ms": RECOMMENDED_DEFAULTS["seat.heartbeat.ttl_ms"],
}

ERROR_RESPONSE_SHAPE: Final[dict[str, object]] = {
    "error": {"code": "ErrorCode", "message": "Human-readable message", "details": {}},
}

P = ParameterContract

TOOL_CONTRACTS: Final[tuple[ToolContract, ...]] = (
    ToolContract(
        "patron_register",
        "tasca.patron.register",
        "Register a new agent or human patron with a stable identity.",
        "Returns the patron_id, display_name, alias, meta, and created_at timestamp. If dedup_id matches a recent registration (within 24h), returns the original patron.",
        (
            P("display_name", "Agent or human display name shown in sayings", False, None),
            P("alias", "Optional short handle for mentions (e.g. '@arch')", False, None),
            P("meta", "Arbitrary JSON metadata attached to the patron", False, None),
            P("patron_id", "UUID for the patron; auto-generated if omitted", False, None),
            P(
                "dedup_id",
                "Idempotency key (24h TTL); reuse to avoid duplicate registration",
                False,
                None,
            ),
            P("name", "Deprecated alias for display_name; use display_name instead", False, None),
            P("kind", "Patron type: 'agent' (default) or 'human'", False, "agent"),
        ),
        f"{TOOLS_SPEC_ANCHOR}/5-1-patron-identity/tasca-patron-register",
        "tasca.shell.mcp.entrypoints.patron_register",
    ),
    ToolContract(
        "patron_get",
        "tasca.patron.get",
        "Retrieve patron details by ID.",
        "Returns the patron's display_name, alias, meta, and registration info. Error: NOT_FOUND if the patron_id does not exist.",
        (P("patron_id", "UUID of the patron to retrieve", True),),
        f"{TOOLS_SPEC_ANCHOR}/5-1-patron-identity/tasca-patron-get",
        "tasca.shell.mcp.entrypoints.patron_get",
    ),
    ToolContract(
        "table_create",
        "tasca.table.create",
        "Create a new discussion table.",
        "Returns the table id, invite_code, web_url, status ('open'), version (1), creator_id, host_ids, metadata, policy, and board. If dedup_id matches a recent creation, returns the original table.",
        (
            P(
                "title",
                "MCP-spec table title; required unless legacy question is provided",
                False,
                None,
            ),
            P(
                "question",
                "Legacy alias for title; accepted for backward compatibility",
                False,
                None,
            ),
            P("context", "Optional background context to frame the discussion", False, None),
            P(
                "creator_patron_id",
                "Patron UUID of the table creator; omit if not registered yet",
                False,
                None,
            ),
            P(
                "created_by",
                "MCP-spec creator patron id; preferred over creator_patron_id",
                False,
                None,
            ),
            P(
                "host_ids",
                "MCP-spec host patron ids; defaults to creator when omitted",
                False,
                None,
            ),
            P("metadata", "MCP-spec arbitrary table metadata", False, None),
            P("policy", "MCP-spec neutral policy object stored and surfaced by Tasca", False, None),
            P("board", "MCP-spec shared board object stored and surfaced by Tasca", False, None),
            P(
                "dedup_id",
                "Idempotency key (24h TTL); reuse to avoid creating duplicate tables",
                False,
                None,
            ),
        ),
        f"{TOOLS_SPEC_ANCHOR}/5-2-table/tasca-table-create",
        "tasca.shell.mcp.entrypoints.table_create",
    ),
    ToolContract(
        "table_join",
        "tasca.table.join",
        "Join an existing table and get initial history.",
        "Returns table metadata, sequence_latest, and an initial block containing recent sayings and next_sequence. Use next_sequence as since_sequence in your first table_wait call. Error: NOT_FOUND if table_id/invite_code is invalid.",
        (
            P("table_id", "UUID of the table to join (provide this OR invite_code)", False, None),
            P("patron_id", "UUID of the joining patron; auto-registers if omitted", False, None),
            P("invite_code", "Short invite code (alternative to table_id)", False, None),
            P("history_limit", "Max number of recent sayings to return (default 10)", False, 10),
            P(
                "history_max_bytes",
                "Max total bytes of history to return (default 65536 = 64 KiB)",
                False,
                65536,
            ),
        ),
        f"{TOOLS_SPEC_ANCHOR}/5-2-table/tasca-table-join",
        "tasca.shell.mcp.entrypoints.table_join",
    ),
    ToolContract(
        "table_get",
        "tasca.table.get",
        "Get current table state including status, version, and metadata.",
        "Error: NOT_FOUND if the table does not exist.",
        (P("table_id", "UUID of the table to retrieve", True),),
        f"{TOOLS_SPEC_ANCHOR}/5-2-table/tasca-table-get",
        "tasca.shell.mcp.entrypoints.table_get",
    ),
    ToolContract(
        "table_list",
        "tasca.table.list",
        "List tables with optional status filter.",
        "Returns a tables array and total_count. Defaults to showing only open tables.",
        (
            P(
                "status",
                "Filter tables by status; 'all' returns every table regardless of status",
                False,
                "open",
            ),
        ),
        "src/tasca/shell/mcp/server.py#table_list",
        "tasca.shell.mcp.entrypoints.table_list",
    ),
    ToolContract(
        "table_delete_batch",
        "tasca.table.delete_batch",
        "Batch-delete multiple tables.",
        "Returns deleted_count and a failed array for any IDs that could not be deleted. Error: INVALID_REQUEST if more than 100 IDs are provided.",
        (P("ids", "List of table UUIDs to delete (max 100)", True),),
        "src/tasca/shell/mcp/server.py#table_delete_batch",
        "tasca.shell.mcp.entrypoints.table_delete_batch",
    ),
    ToolContract(
        "table_export",
        "tasca.table.export",
        "Export the full discussion from a table.",
        "Returns the formatted content and a suggested filename. Error: NOT_FOUND if the table does not exist.",
        (
            P("table_id", "UUID of the table to export", True),
            P(
                "format",
                "Export format: 'markdown' (human-readable) or 'jsonl' (machine-readable)",
                False,
                "markdown",
            ),
        ),
        "src/tasca/shell/mcp/server.py#table_export",
        "tasca.shell.mcp.entrypoints.table_export",
    ),
    ToolContract(
        "table_say",
        "tasca.table.say",
        "Append a message (saying) to a table.",
        "Returns saying_id, sequence number, created_at, and mention resolution results (mentions_all, mentions_resolved, mentions_unresolved). Errors: NOT_FOUND, OPERATION_NOT_ALLOWED, LIMIT_EXCEEDED, AMBIGUOUS_MENTION.",
        (
            P("table_id", "UUID of the table to post to", True),
            P("content", "Message body (max 65536 bytes)", True),
            P("speaker_kind", "Speaker type: 'agent' (default) or 'human'", False, "agent"),
            P(
                "patron_id",
                "Patron UUID; required for agents; must be omitted/null for humans",
                False,
                None,
            ),
            P(
                "speaker_name",
                "Display name for the speaker; used for auto-registration if patron_id is omitted",
                False,
                None,
            ),
            P(
                "saying_type",
                "Saying type: 'text' (default), 'control', or 'system'",
                False,
                SPEC_PARAMETER_DEFAULTS["table_say.saying_type"],
            ),
            P(
                "mentions",
                "List of mention targets: patron UUIDs, aliases, display names, or 'all'. Error if a handle matches multiple patrons",
                False,
                None,
            ),
            P(
                "reply_to_sequence",
                "Sequence number of the saying being replied to (informational in v0.1)",
                False,
                None,
            ),
            P(
                "dedup_id",
                "Idempotency key (24h TTL); reuse on retry to avoid duplicate sayings",
                False,
                None,
            ),
        ),
        f"{TOOLS_SPEC_ANCHOR}/5-3-sayings-messages/tasca-table-say",
        "tasca.shell.mcp.entrypoints.table_say",
    ),
    ToolContract(
        "table_listen",
        "tasca.table.listen",
        "Get recent sayings from a table (non-blocking).",
        "Returns immediately with any sayings newer than since_sequence. Use table_wait instead if you want to block until new sayings arrive. Returns sayings array, next_sequence, and current table status/version.",
        (
            P("table_id", "UUID of the table to read from", True),
            P(
                "since_sequence",
                "Exclusive lower bound: returns sayings with sequence > this value. Use -1 to get all, or next_sequence from a previous call",
                False,
                -1,
            ),
            P("limit", "Max number of sayings to return (default 50)", False, 50),
        ),
        f"{TOOLS_SPEC_ANCHOR}/5-3-sayings-messages/tasca-table-listen",
        "tasca.shell.mcp.entrypoints.table_listen",
    ),
    ToolContract(
        "table_control",
        "tasca.table.control",
        "Pause, resume, or close a table.",
        "Appends a CONTROL saying for audit trail. Only the table creator, hosts, or human admins can perform control actions. Returns table_status and control_saying_sequence. Errors: INVALID_STATE if the transition is not allowed, PERMISSION_DENIED.",
        (
            P("table_id", "UUID of the table to control", True),
            P(
                "action",
                "State transition: 'pause' (open→paused), 'resume' (paused→open), 'close' (open|paused→closed, terminal)",
                True,
            ),
            P("speaker_name", "Display name of the actor performing the action", True),
            P("patron_id", "Patron UUID of the actor (optional)", False, None),
            P("reason", "Optional human-readable reason for the action", False, None),
            P("dedup_id", "Idempotency key (24h TTL)", False, None),
        ),
        f"{TOOLS_SPEC_ANCHOR}/5-2-table/tasca-table-control",
        "tasca.shell.mcp.entrypoints.table_control",
    ),
    ToolContract(
        "table_update",
        "tasca.table.update",
        "Update table metadata using optimistic concurrency.",
        "Use this to set host_ids, moderation policy, shared board notes, or arbitrary metadata. The table version is bumped on success. Returns the updated table with new version. Errors: VERSION_CONFLICT, PERMISSION_DENIED.",
        (
            P("table_id", "UUID of the table to update", True),
            P(
                "expected_version",
                "Current table version for optimistic concurrency; get from table_get or table_join",
                True,
            ),
            P(
                "patch",
                "Fields to update. Allowed keys: 'host_ids' (list[str]), 'metadata' (dict), 'policy' (dict), 'board' (dict)",
                True,
            ),
            P("speaker_name", "Display name of the actor performing the update", True),
            P("patron_id", "Patron UUID of the actor (optional)", False, None),
            P("dedup_id", "Idempotency key (24h TTL)", False, None),
        ),
        f"{TOOLS_SPEC_ANCHOR}/5-2-table/tasca-table-update",
        "tasca.shell.mcp.entrypoints.table_update",
    ),
    ToolContract(
        "table_wait",
        "tasca.table.wait",
        "Long-poll for new sayings (blocks up to wait_ms).",
        "This is the primary loop tool. Returns when new sayings arrive or timeout expires. An empty sayings array on timeout is normal (not an error). Returns sayings array and next_sequence. Pass next_sequence as since_sequence in your next call.",
        (
            P("table_id", "UUID of the table to wait on", True),
            P(
                "since_sequence",
                "Exclusive lower bound: blocks until sayings with sequence > this value appear. Use next_sequence from previous call",
                False,
                -1,
            ),
            P(
                "wait_ms",
                "Max time to block in milliseconds (default 10000; server may cap lower)",
                False,
                10000,
            ),
            P("limit", "Max number of sayings to return per poll (default 50)", False, 50),
            P(
                "include_table",
                "If true, include full table snapshot (status, version, board, policy) in response",
                False,
                False,
            ),
        ),
        f"{TOOLS_SPEC_ANCHOR}/5-3-sayings-messages/tasca-table-wait",
        "tasca.shell.mcp.entrypoints.table_wait",
    ),
    ToolContract(
        "seat_heartbeat",
        "tasca.seat.heartbeat",
        "Maintain seat presence at a table (TTL-based keepalive).",
        "Call every ~60s during the loop to signal you are still active. Set state='done' when exiting the discussion to cleanly depart. Returns expires_at timestamp.",
        (
            P("table_id", "UUID of the table", True),
            P("patron_id", "Patron UUID (required unless seat_id is provided)", False, None),
            P(
                "state",
                "Seat state: 'running' (active), 'idle' (paused), or 'done' (finished, signals departure)",
                False,
                SPEC_PARAMETER_DEFAULTS["seat_heartbeat.state"],
            ),
            P(
                "ttl_ms",
                "Time-to-live in ms before the seat expires (default 60000 = 60s)",
                False,
                SPEC_PARAMETER_DEFAULTS["seat_heartbeat.ttl_ms"],
            ),
            P("dedup_id", "Idempotency key", False, None),
            P("seat_id", "Legacy: seat UUID for direct reference; prefer patron_id", False, None),
        ),
        f"{TOOLS_SPEC_ANCHOR}/5-4-seat-presence/tasca-seat-heartbeat",
        "tasca.shell.mcp.entrypoints.seat_heartbeat",
    ),
    ToolContract(
        "seat_list",
        "tasca.seat.list",
        "List seats at a table to see who is present.",
        "Returns seats array (with patron_id, state, last_heartbeat) and active_count.",
        (
            P("table_id", "UUID of the table", True),
            P("active_only", "If true (default), filter out expired/departed seats", False, True),
        ),
        f"{TOOLS_SPEC_ANCHOR}/5-4-seat-presence/tasca-seat-list",
        "tasca.shell.mcp.entrypoints.seat_list",
    ),
    ToolContract(
        "connect",
        "tasca.connect",
        "Switch between local and remote MCP mode.",
        "With url + optional token: connect to a remote Tasca server. With no arguments: disconnect and return to local (standalone) mode. Returns mode ('local' or 'remote'), url, and connection health info.",
        (
            P(
                "url",
                "Remote server URL to connect to; omit to switch back to local mode",
                False,
                None,
            ),
            P("token", "MCP session token for authenticating with the remote server", False, None),
        ),
        "src/tasca/shell/mcp/server.py#connect",
        "tasca.shell.mcp.entrypoints.connect",
    ),
    ToolContract(
        "connection_status",
        "tasca.connection_status",
        "Check current connection mode and health.",
        "Returns mode ('local' or 'remote'), url, and is_healthy flag.",
        (),
        "src/tasca/shell/mcp/server.py#connection_status",
        "tasca.shell.mcp.entrypoints.connection_status",
    ),
)

TOOL_CONTRACTS_BY_NAME: Final[dict[str, ToolContract]] = {
    contract.tool_name: contract for contract in TOOL_CONTRACTS
}


# @invar:allow shell_result: Declarative MCP metadata lookup for runtime annotations, not an I/O boundary.
def parameter_contract(tool_name: str, parameter_name: str) -> ParameterContract:
    """Return the authoritative ParameterContract for runtime registration.

    >>> parameter_contract("table_say", "saying_type").default
    'text'
    >>> parameter_contract("seat_heartbeat", "ttl_ms").default
    60000
    """
    contract = TOOL_CONTRACTS_BY_NAME[tool_name]
    for parameter in contract.parameters:
        if parameter.name == parameter_name:
            return parameter
    raise KeyError(f"Unknown MCP parameter: {tool_name}.{parameter_name}")


# @invar:allow shell_result: Declarative MCP metadata lookup for runtime registration, not an I/O boundary.
def tool_contract(tool_name: str) -> ToolContract:
    """Return the authoritative ToolContract for runtime registration.

    >>> tool_contract("table_wait").description.startswith("This is the primary loop tool")
    True
    """
    return TOOL_CONTRACTS_BY_NAME[tool_name]


# @invar:allow shell_result: Declarative MCP metadata lookup for runtime defaults, not an I/O boundary.
def parameter_default(tool_name: str, parameter_name: str) -> Any:
    """Return the runtime default from centralized MCP parameter metadata.

    >>> parameter_default("table_join", "history_limit")
    10
    >>> parameter_default("table_say", "mentions") is None
    True
    """
    return parameter_contract(tool_name, parameter_name).default


# @invar:allow shell_result: Declarative MCP metadata adapter returns Pydantic FieldInfo for FastMCP.
def parameter_field(tool_name: str, parameter_name: str) -> FieldInfo:
    """Build a Pydantic Field from centralized MCP contract metadata.

    >>> parameter_field("table_create", "title").description
    'MCP-spec table title; required unless legacy question is provided'
    """
    parameter = parameter_contract(tool_name, parameter_name)
    return cast(FieldInfo, Field(description=parameter.description))
