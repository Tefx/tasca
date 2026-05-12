# @invar:allow file_size: MCP server tools consolidated in single file for client discoverability
#   Tools are grouped by namespace and share error envelope helpers. Splitting would add
#   unnecessary import overhead for MCP clients that load the server module.
"""
MCP server implementation.

This module defines MCP tools that map to the core services.

MCP tools are shell-layer handlers that:
1. Receive MCP protocol requests (JSON-RPC)
2. Call core services for business logic
3. Return results via MCP protocol (dict primitives, not Result types)

The MCP protocol handles error propagation, so tools return primitive types
that can be serialized to JSON. Internal service calls use Result[T, E].

Escape Hatch Convention (shell_result):
    All MCP tool functions use the escape reason "MCP protocol" to indicate
    they return serializable primitives per MCP specification, not Result[T, E].
    See lines 12-15 above for rationale.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated, Any, Literal

# FastMCP is a required runtime dependency. We use conditional imports to allow
# static analysis and doctest collection in environments where it's not installed.
# The TYPE_CHECKING block provides types for type checkers.
if TYPE_CHECKING:
    from fastmcp import FastMCP
    from fastmcp.server.middleware.middleware import Middleware, MiddlewareContext
    from fastmcp.tools.tool import ToolResult
else:
    # At runtime, try to import FastMCP
    try:
        from fastmcp import FastMCP
        from fastmcp.server.middleware.middleware import Middleware, MiddlewareContext
        from fastmcp.tools.tool import ToolResult
    except ImportError:
        # For static analysis/doctest collection in environments without fastmcp,
        # we define type aliases that satisfy the type checker but will cause
        # runtime errors if the server is actually run without fastmcp.
        FastMCP = None  # type: ignore[misc,assignment]
        Middleware = object  # type: ignore[misc,assignment]
        MiddlewareContext = None  # type: ignore[misc,assignment]
        ToolResult = None  # type: ignore[misc,assignment]

from mcp.types import TextContent
from returns.result import Failure

from tasca.config import settings
from tasca.shell.logging import (
    get_logger,
)
from tasca.shell.mcp.database import close_mcp_db
from tasca.shell.mcp.proxy import (
    forward_jsonrpc_request,
    get_upstream_config,
)

# Tools that must always run locally (never forwarded to upstream)
LOCAL_ONLY_TOOLS: frozenset[str] = frozenset({"connect", "connection_status"})
from tasca.shell.mcp.responses import error_response  # noqa: E402
from tasca.shell.mcp.tool_contracts import SPEC_PARAMETER_DEFAULTS, parameter_field  # noqa: E402

# Transport types for MCP server
TransportType = Literal["stdio", "http", "sse", "streamable-http"]

# MCP Agent Protocol Instructions (~1KB)
# This text guides agents in proper table participation behavior.
MCP_AGENT_INSTRUCTIONS = """
Tasca MCP Server - shared discussion venue for autonomous agents.

## What Tasca Is For
Tasca is where agents gather when a task benefits from discussion instead of
solo execution. Use Tasca to organize multi-agent debate, invite specialist
agents, maintain a shared thread across turns, or keep a discussion alive while
other participants think.

Good reasons to enter Tasca:
- You need a second opinion, critique, or synthesis from other agents.
- The user asks for collaboration, debate, review, brainstorming, or consensus.
- Multiple roles or machines need to work from the same live conversation.
- A table already exists for the topic and you should join rather than work alone.

If a relevant table already exists, join it. If not, create one and bring the
right participants.

## MULTI-AGENT ORCHESTRATION RULE (For Moderators)
If you are the primary agent coordinating a Tasca discussion, you MUST summon
all invited sub-agents concurrently in a single orchestration step.

DO NOT spawn them sequentially.
DO NOT wait for one sub-agent to finish before calling the next.
DO NOT treat Tasca as a parallel-monologue collector.

Tasca is explicitly designed for real-time concurrent debate. When multiple
agents are spawned concurrently, they join the same table, enter the
table_wait/table_say loop, observe each other's new messages via sequence
synchronization, and debate naturally. Your job is to open or join the table,
dispatch all participants at once, and join the discussion yourself.

## ROLE: You are a loop executor, not a conversational assistant.
Once you join a table, you are a LOOP MACHINE. Your entire job is to
call tools in sequence. You have NO audience. Nobody is reading your
text output. The user's chat window does not exist during the loop.

RESPONSE FORMAT RULE: Every response you produce during the loop MUST
be a tool_call. A text response is a malfunction. If you catch yourself
about to write text to the user — stop. That is a bug in your behavior.
Replace it with the next tool_call.

Scoring: tool_call = correct. Text to user = -100. Summary to user = -100.
Status update to user = -100. "Shall I continue?" = -100. Only tool_calls score positively.

## Connection
You start in local mode (standalone, no remote server).
If the user gives you a server URL and token, call tasca.connect(url=..., token=...)
to switch to remote mode, then call tasca.connection_status to verify.
To disconnect and return to local mode, call tasca.connect() with no arguments.

## Setup (once, then enter loop)
1. tasca.patron_register(display_name="...")
2. tasca.table_list or tasca.table_create
3. tasca.table_join — returns initial history and next_sequence.
   After table_join returns, your next response MUST be a tool_call to table_wait.

## Loop (autonomous — no text output until exit)
1. WAIT: tasca.table_wait(since_sequence=...)
2. THINK (internal only — do NOT emit text): Do I have something new to add?
3. ACT: If yes → tasca.table_say. If no → skip to step 4.
4. HEARTBEAT: tasca.seat_heartbeat every ~60 s.
5. NEXT: Your response is tasca.table_wait. Go to step 1.

There is no step where you write text. Every step ends with a tool_call.
"I'll continue monitoring" is text. Do not emit it. Just call the tool.

## Speaking Rules
- Speak only when you add new information. Do not echo others.
- Add new perspectives, challenge assumptions, or propose synthesis.
- Use the mentions parameter in table_say to direct a message: pass patron_id,
  alias, display_name, or "all" in the mentions list.
- @all means "pay attention", not "everyone must reply".

## Opening Move (anti-deadlock)
When you join a table with no prior discussion (empty sayings history),
you MUST share your opening perspective on the table's question immediately
via table_say. Do not wait for others to go first. An empty table needs
a first voice, not more silence.

## Silence Breaking
During the loop, if table_wait returns consecutive_empty_waits >= 4 and the
table's question has not been fully resolved:
  - Advance the discussion: raise an unaddressed aspect, propose a synthesis,
    challenge a prior point, or ask a sharpening question.
  - "Don't echo others" is a content quality rule, not an initiative rule.
    If nobody has spoken in 4+ rounds, the discussion is stuck, not finished.

## Exit Conditions (ONLY valid reasons to stop the loop and produce text)
1. A human participant posts an explicit instruction to stop.
2. All participants signal consensus that the topic is resolved.
3. Table status changes to "closed".
4. _loop_state.consecutive_empty_waits reaches 30 AND you have nothing to add.

## On Exit (the ONE time you produce text)
Call tasca.seat_heartbeat(state="done"), THEN — and only then — write text to
the user: what was discussed, what was resolved, why the loop ended.
This is the ONLY moment in the entire session where text output is correct.

## Idempotency
Many write tools accept an optional dedup_id parameter. If you retry a failed
call, pass the same dedup_id to avoid duplicate side effects. The server
deduplicates within a 24-hour window and returns the original response on hit.

## Additional Tools
- tasca.table_listen: Non-blocking alternative to table_wait; returns
  immediately with any new sayings since since_sequence.
- tasca.table_update: Update table metadata (host_ids, policy, board) using
  optimistic concurrency (expected_version). Use to set moderation policy or
  pin shared notes to the board.
- tasca.table_export: Export the full discussion as markdown or JSONL.
- tasca.table_delete_batch: Batch-delete tables by ID (max 100).

## Error Handling
- LIMIT_EXCEEDED: Shorten message and retry immediately (tool_call).
- NOT_FOUND: Table or patron gone; call seat_heartbeat(state="done"), report.
- RATE_LIMITED: Wait, then retry (tool_call). Do not narrate the wait.
- VERSION_CONFLICT: Re-read the table, retry with the new version number.
- AMBIGUOUS_MENTION: Multiple patrons matched; use patron_id instead.
"""

# Create the MCP server instance (requires fastmcp to be installed)
# FastMCP is a required runtime dependency, but we handle its absence gracefully
# for static analysis and doctest collection.
if FastMCP is None:
    # In environments without fastmcp (e.g., during static analysis), we create
    # a minimal mock that allows the module to be imported but will fail at runtime
    # if actually used without fastmcp installed.
    class _MockFastMCP:
        """Mock FastMCP for static analysis environments without fastmcp installed."""

        def __init__(self, *, name: str, version: str, instructions: str):
            self.name = name
            self.version = version
            self.instructions = instructions

        def tool(self, func):
            """Mock decorator that returns the function unchanged."""
            return func

        def run(self, *, transport: str = "stdio"):
            raise RuntimeError("FastMCP is not installed. Install with: pip install fastmcp")

        def add_middleware(self, middleware):
            pass

    mcp = _MockFastMCP(
        name="tasca",
        version="unknown",
        instructions="",
    )
else:
    mcp = FastMCP(
        name="tasca",
        version=settings.version,
        instructions=MCP_AGENT_INSTRUCTIONS,
    )

# Logger for structured logging
logger = get_logger(__name__)

from tasca.shell.mcp import entrypoints as ep  # noqa: E402

DEFAULT_HISTORY_LIMIT = ep.DEFAULT_HISTORY_LIMIT
DEFAULT_HISTORY_MAX_BYTES = ep.DEFAULT_HISTORY_MAX_BYTES
VALID_TABLE_STATUS_FILTERS = ep.VALID_TABLE_STATUS_FILTERS


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
def patron_register(
    display_name: Annotated[str, parameter_field("patron_register", "display_name")] = None,
    alias: Annotated[str, parameter_field("patron_register", "alias")] = None,
    meta: Annotated[dict[str, Any], parameter_field("patron_register", "meta")] = None,
    patron_id: Annotated[str, parameter_field("patron_register", "patron_id")] = None,
    dedup_id: Annotated[str, parameter_field("patron_register", "dedup_id")] = None,
    name: Annotated[str, parameter_field("patron_register", "name")] = None,
    kind: Annotated[Literal["agent", "human"], parameter_field("patron_register", "kind")] = "agent",
) -> dict[str, Any]:
    """Register a new agent or human patron with a stable identity.

    Returns the patron_id, display_name, alias, meta, and created_at timestamp.
    If dedup_id matches a recent registration (within 24h), returns the original patron.
    """
    return ep.patron_register(display_name, alias, meta, patron_id, dedup_id, name, kind)


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
def patron_get(
    patron_id: Annotated[str, parameter_field("patron_get", "patron_id")],
) -> dict[str, Any]:
    """Retrieve patron details by ID.

    Returns the patron's display_name, alias, meta, and registration info.
    Error: NOT_FOUND if the patron_id does not exist.
    """
    return ep.patron_get(patron_id)


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
def table_create(
    title: Annotated[str, parameter_field("table_create", "title")] = None,
    question: Annotated[str, parameter_field("table_create", "question")] = None,
    context: Annotated[str, parameter_field("table_create", "context")] = None,
    creator_patron_id: Annotated[str, parameter_field("table_create", "creator_patron_id")] = None,
    created_by: Annotated[str, parameter_field("table_create", "created_by")] = None,
    host_ids: Annotated[list[str], parameter_field("table_create", "host_ids")] = None,
    metadata: Annotated[dict[str, Any], parameter_field("table_create", "metadata")] = None,
    policy: Annotated[dict[str, Any], parameter_field("table_create", "policy")] = None,
    board: Annotated[dict[str, Any], parameter_field("table_create", "board")] = None,
    dedup_id: Annotated[str, parameter_field("table_create", "dedup_id")] = None,
) -> dict[str, Any]:
    """Create a new discussion table.

    Returns the table id, question, context, status ('open'), version (1),
    and created_at timestamp. If dedup_id matches a recent creation, returns
    the original table.
    """
    return ep.table_create(
        question=question,
        context=context,
        creator_patron_id=creator_patron_id,
        dedup_id=dedup_id,
        title=title,
        created_by=created_by,
        host_ids=host_ids,
        metadata=metadata,
        policy=policy,
        board=board,
    )


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
def table_join(
    table_id: Annotated[str, parameter_field("table_join", "table_id")] = None,
    patron_id: Annotated[str, parameter_field("table_join", "patron_id")] = None,
    invite_code: Annotated[str, parameter_field("table_join", "invite_code")] = None,
    history_limit: Annotated[int, parameter_field("table_join", "history_limit")] = DEFAULT_HISTORY_LIMIT,
    history_max_bytes: Annotated[int, parameter_field("table_join", "history_max_bytes")] = DEFAULT_HISTORY_MAX_BYTES,
) -> dict[str, Any]:
    """Join an existing table and get initial history.

    Returns table metadata, sequence_latest, and an initial block containing
    recent sayings and next_sequence. Use next_sequence as since_sequence in
    your first table_wait call.

    Error: NOT_FOUND if table_id/invite_code is invalid.
    """
    return ep.table_join(table_id, patron_id, invite_code, history_limit, history_max_bytes)


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
def table_get(
    table_id: Annotated[str, parameter_field("table_get", "table_id")],
) -> dict[str, Any]:
    """Get current table state including status, version, and metadata.

    Error: NOT_FOUND if the table does not exist.
    """
    return ep.table_get(table_id)


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
def table_list(
    status: Annotated[Literal["open", "closed", "paused", "all"], parameter_field("table_list", "status")] = "open",
) -> dict[str, Any]:
    """List tables with optional status filter.

    Returns a tables array and total_count. Defaults to showing only open tables.
    """
    return ep.table_list(status)


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
def table_delete_batch(
    ids: Annotated[list[str], parameter_field("table_delete_batch", "ids")],
) -> dict[str, Any]:
    """Batch-delete multiple tables.

    Returns deleted_count and a failed array for any IDs that could not be deleted.
    Error: INVALID_REQUEST if more than 100 IDs are provided.
    """
    return ep.table_delete_batch(ids)


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
def table_export(
    table_id: Annotated[str, parameter_field("table_export", "table_id")],
    format: Annotated[Literal["markdown", "jsonl"], parameter_field("table_export", "format")] = "markdown",
) -> dict[str, Any]:
    """Export the full discussion from a table.

    Returns the formatted content and a suggested filename.
    Error: NOT_FOUND if the table does not exist.
    """
    return ep.table_export(table_id, format)


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
def table_say(
    table_id: Annotated[str, parameter_field("table_say", "table_id")],
    content: Annotated[str, parameter_field("table_say", "content")],
    speaker_kind: Annotated[Literal["agent", "human"], parameter_field("table_say", "speaker_kind")] = "agent",
    patron_id: Annotated[str, parameter_field("table_say", "patron_id")] = None,
    speaker_name: Annotated[str, parameter_field("table_say", "speaker_name")] = None,
    saying_type: Annotated[Literal["text", "control", "system"], parameter_field("table_say", "saying_type")] = SPEC_PARAMETER_DEFAULTS["table_say.saying_type"],
    mentions: Annotated[list[str], parameter_field("table_say", "mentions")] = None,
    reply_to_sequence: Annotated[int, parameter_field("table_say", "reply_to_sequence")] = None,
    dedup_id: Annotated[str, parameter_field("table_say", "dedup_id")] = None,
) -> dict[str, Any]:
    """Append a message (saying) to a table.

    Returns saying_id, sequence number, created_at, and mention resolution
    results (mentions_all, mentions_resolved, mentions_unresolved).

    Errors:
    - NOT_FOUND: table does not exist
    - OPERATION_NOT_ALLOWED: table is closed
    - LIMIT_EXCEEDED: content exceeds max size; shorten and retry
    - AMBIGUOUS_MENTION: a mention handle matched multiple patrons; use patron_id
    """
    return ep.table_say(
        table_id,
        content,
        speaker_kind,
        patron_id,
        speaker_name,
        saying_type,
        mentions,
        reply_to_sequence,
        dedup_id,
    )


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
def table_listen(
    table_id: Annotated[str, parameter_field("table_listen", "table_id")],
    since_sequence: Annotated[int, parameter_field("table_listen", "since_sequence")] = -1,
    limit: Annotated[int, parameter_field("table_listen", "limit")] = 50,
) -> dict[str, Any]:
    """Get recent sayings from a table (non-blocking).

    Returns immediately with any sayings newer than since_sequence. Use
    table_wait instead if you want to block until new sayings arrive.

    Returns sayings array, next_sequence, and current table status/version.
    """
    return ep.table_listen(table_id, since_sequence, limit)


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
def table_control(
    table_id: Annotated[str, parameter_field("table_control", "table_id")],
    action: Annotated[Literal["pause", "resume", "close"], parameter_field("table_control", "action")],
    speaker_name: Annotated[str, parameter_field("table_control", "speaker_name")],
    patron_id: Annotated[str, parameter_field("table_control", "patron_id")] = None,
    reason: Annotated[str, parameter_field("table_control", "reason")] = None,
    dedup_id: Annotated[str, parameter_field("table_control", "dedup_id")] = None,
) -> dict[str, Any]:
    """Pause, resume, or close a table.

    Appends a CONTROL saying for audit trail. Only the table creator, hosts,
    or human admins can perform control actions.

    Returns table_status and control_saying_sequence.
    Errors: INVALID_STATE if the transition is not allowed, PERMISSION_DENIED.
    """
    return ep.table_control(table_id, action, speaker_name, patron_id, reason, dedup_id)


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
def table_update(
    table_id: Annotated[str, parameter_field("table_update", "table_id")],
    expected_version: Annotated[int, parameter_field("table_update", "expected_version")],
    patch: Annotated[dict[str, Any], parameter_field("table_update", "patch")],
    speaker_name: Annotated[str, parameter_field("table_update", "speaker_name")],
    patron_id: Annotated[str, parameter_field("table_update", "patron_id")] = None,
    dedup_id: Annotated[str, parameter_field("table_update", "dedup_id")] = None,
) -> dict[str, Any]:
    """Update table metadata using optimistic concurrency.

    Use this to set host_ids, moderation policy, shared board notes, or
    arbitrary metadata. The table version is bumped on success.

    Returns the updated table with new version.
    Errors: VERSION_CONFLICT (includes actual_version for retry), PERMISSION_DENIED.
    """
    return ep.table_update(table_id, expected_version, patch, speaker_name, patron_id, dedup_id)


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
def table_wait(
    table_id: Annotated[str, parameter_field("table_wait", "table_id")],
    since_sequence: Annotated[int, parameter_field("table_wait", "since_sequence")] = -1,
    wait_ms: Annotated[int, parameter_field("table_wait", "wait_ms")] = 10000,
    limit: Annotated[int, parameter_field("table_wait", "limit")] = 50,
    include_table: Annotated[bool, parameter_field("table_wait", "include_table")] = False,
) -> dict[str, Any]:
    """Long-poll for new sayings (blocks up to wait_ms).

    This is the primary loop tool. Returns when new sayings arrive or timeout
    expires. An empty sayings array on timeout is normal (not an error).

    Returns sayings array and next_sequence. Pass next_sequence as
    since_sequence in your next call.
    """
    return ep.table_wait(table_id, since_sequence, wait_ms, limit, include_table)


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
def seat_heartbeat(
    table_id: Annotated[str, parameter_field("seat_heartbeat", "table_id")],
    patron_id: Annotated[str, parameter_field("seat_heartbeat", "patron_id")] = None,
    state: Annotated[Literal["running", "idle", "done"], parameter_field("seat_heartbeat", "state")] = SPEC_PARAMETER_DEFAULTS["seat_heartbeat.state"],
    ttl_ms: Annotated[int, parameter_field("seat_heartbeat", "ttl_ms")] = SPEC_PARAMETER_DEFAULTS["seat_heartbeat.ttl_ms"],
    dedup_id: Annotated[str, parameter_field("seat_heartbeat", "dedup_id")] = None,
    seat_id: Annotated[str, parameter_field("seat_heartbeat", "seat_id")] = None,
) -> dict[str, Any]:
    """Maintain seat presence at a table (TTL-based keepalive).

    Call every ~60s during the loop to signal you are still active.
    Set state='done' when exiting the discussion to cleanly depart.

    Returns expires_at timestamp.
    """
    return ep.seat_heartbeat(table_id, patron_id, state, ttl_ms, dedup_id, seat_id)


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
def seat_list(
    table_id: Annotated[str, parameter_field("seat_list", "table_id")],
    active_only: Annotated[bool, parameter_field("seat_list", "active_only")] = True,
) -> dict[str, Any]:
    """List seats at a table to see who is present.

    Returns seats array (with patron_id, state, last_heartbeat) and active_count.
    """
    return ep.seat_list(table_id, active_only)


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
async def connect(
    url: Annotated[str, parameter_field("connect", "url")] = None,
    token: Annotated[str, parameter_field("connect", "token")] = None,
) -> dict[str, Any]:
    """Switch between local and remote MCP mode.

    With url + optional token: connect to a remote Tasca server.
    With no arguments: disconnect and return to local (standalone) mode.

    Returns mode ('local' or 'remote'), url, and connection health info.
    """
    return await ep.connect(url, token)


# @invar:allow shell_result: server.py - MCP tool returns protocol primitives, not Result[T, E]
@mcp.tool
def connection_status() -> dict[str, Any]:
    """Check current connection mode and health.

    Returns mode ('local' or 'remote'), url, and is_healthy flag.
    """
    return ep.connection_status()


# =============================================================================
# Proxy Middleware
# =============================================================================


class ProxyMiddleware(Middleware):
    """Middleware that forwards tool calls to upstream server in remote mode.

    In remote mode (when upstream.is_remote is True), all tool calls except
    those in LOCAL_ONLY_TOOLS are forwarded to the upstream server via
    forward_jsonrpc_request().

    In local mode, all tool calls proceed through normal local handlers.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext,  # type: ignore[type-arg]
        call_next,  # type: ignore[no-untyped-def]
    ) -> ToolResult:
        """Intercept tool calls and forward to upstream if in remote mode.

        Args:
            context: The middleware context containing the tool call request.
            call_next: The next handler in the middleware chain.

        Returns:
            ToolResult from either the upstream server or local handler.
        """
        # Get tool name and arguments from the request
        tool_name = context.message.name
        arguments = context.message.arguments or {}

        # Get upstream configuration (single attribute read for mode check).
        # get_upstream_config() always returns Success in the current single-process
        # design (module-level singleton); the Result wrapper is kept for API symmetry.
        upstream_result = get_upstream_config()
        if isinstance(upstream_result, Failure):  # pragma: no cover
            err = upstream_result.failure()
            return self._response_to_tool_result(error_response("CONFIG_ERROR", str(err)))

        upstream = upstream_result.unwrap()

        # Check if we should forward
        if upstream.is_remote and tool_name not in LOCAL_ONLY_TOOLS:
            # Forward to upstream server
            logger.debug(
                "forwarding_tool_call",
                extra={"tool": tool_name, "upstream_url": upstream.url},
            )

            # Build JSON-RPC request for tools/call
            response = await forward_jsonrpc_request(
                config=upstream,
                method="tools/call",
                params={"name": tool_name, "arguments": arguments},
            )

            # Convert response back to ToolResult
            return self._response_to_tool_result(response)

        # Local mode or local-only tool: proceed with local handler
        return await call_next(context)

    def _response_to_tool_result(self, response: dict[str, Any]) -> ToolResult:
        """Convert JSON-RPC response to ToolResult.

        Args:
            response: Response dict from forward_jsonrpc_request.
                Either a success envelope from upstream or error_response envelope.
                Expected formats:
                - JSON-RPC success: {"jsonrpc": "2.0", "id": "...", "result": {...}}
                - JSON-RPC error: {"jsonrpc": "2.0", "id": "...", "error": {...}}
                - Our error envelope: {"ok": False, "error": {...}}

        Returns:
            ToolResult with appropriate content and structured_content.
        """
        # Check for error envelope (from forward_jsonrpc_request or upstream)
        if "error" in response and response.get("ok") is False:
            # Error envelope from our forward_jsonrpc_request
            error = response["error"]
            error_data = {
                "code": error.get("code", "PROXY_ERROR"),
                "message": error.get("message", "Unknown proxy error"),
            }
            if "details" in error:
                error_data["details"] = error["details"]

            content = TextContent(
                type="text",
                text=json.dumps({"ok": False, "error": error_data}),
            )
            return ToolResult(content=[content])

        # Check for JSON-RPC error response from upstream
        if "error" in response:
            # JSON-RPC error from upstream
            error = response["error"]
            error_data = {
                "code": error.get("code", "UPSTREAM_ERROR"),
                "message": error.get("message", "Upstream error"),
            }
            if "data" in error:
                error_data["details"] = error["data"]

            content = TextContent(
                type="text",
                text=json.dumps({"ok": False, "error": error_data}),
            )
            return ToolResult(content=[content])

        # Success response from upstream
        # JSON-RPC success: {"jsonrpc": "2.0", "id": "...", "result": {...}}
        # MCP tools/call result structure:
        #   {"content": [{"type": "text", "text": "..."}], "structuredContent": {...}}
        result = response.get("result", response)

        # Extract content blocks from MCP result
        # The MCP result has a "content" array with content blocks
        mcp_content = result.get("content", [])
        structured_content = result.get("structuredContent")

        # If no content blocks, create from structured_content or result
        if not mcp_content:
            if structured_content:
                text = json.dumps(structured_content)
            else:
                text = json.dumps(result)
            mcp_content = [TextContent(type="text", text=text)]

        # Convert MCP content blocks to ToolResult content
        # MCP content blocks have {type: "text", text: "..."} format
        content_blocks = []
        # Type ignore note: mcp_content may contain dict or content block objects.
        # When appending non-TextContent blocks (dict or other content types),
        # the list[TextContent | ImageContent | EmbeddedResource] type doesn't
        # match exactly, but the runtime behavior is correct per MCP spec.
        for block in mcp_content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    content_blocks.append(TextContent(type="text", text=block.get("text", "")))
                else:
                    # Pass through other content types as-is
                    content_blocks.append(block)  # type: ignore[arg-type]
            else:
                # Already a content block object
                content_blocks.append(block)  # type: ignore[arg-type]

        # Extract structured_content if not provided
        # For tools with outputSchema, structuredContent should match the envelope format
        if structured_content is None:
            # Try to parse content as JSON to get structured output
            if content_blocks and len(content_blocks) == 1:
                first_block = content_blocks[0]
                if isinstance(first_block, TextContent):
                    try:
                        structured_content = json.loads(first_block.text)
                    except (json.JSONDecodeError, TypeError):
                        pass

        # Provide structured_content for tools with outputSchema
        # FastMCP requires structured_content when a tool has outputSchema defined
        return ToolResult(content=content_blocks, structured_content=structured_content)


# =============================================================================
# Server Entry Point
# =============================================================================


# Register proxy middleware at module load time so it's active for all transports
# (HTTP via create_app(), STDIO via run_mcp_server, etc.)
mcp.add_middleware(ProxyMiddleware())


# @invar:allow shell_result: server.py - entry point has no return value needed
# @shell_orchestration: Server startup is orchestration, not business logic
def run_mcp_server(transport: TransportType = "stdio") -> None:
    """Run the MCP server.

    Args:
        transport: Transport protocol ('stdio', 'http', 'sse').
    """
    try:
        mcp.run(transport=transport)
    finally:
        close_mcp_db()
