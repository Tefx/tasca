"""
MCP server implementation.

This module defines MCP tools that map to the core services.

MCP tools are shell-layer handlers that:
1. Receive MCP protocol requests (JSON-RPC)
2. Call core services for business logic
3. Return results via MCP protocol (dict primitives, not Result types)

The MCP protocol handles error propagation, so tools return primitive types
that can be serialized to JSON. Internal service calls use Result[T, E].
"""

from typing import Literal

from fastmcp import FastMCP

from tasca.config import settings

# Transport types for MCP server
TransportType = Literal["stdio", "http", "sse", "streamable-http"]

# Create the MCP server instance
mcp = FastMCP(
    name="tasca",
    version=settings.version,
    instructions=(
        "Tasca MCP Server - A discussion table service for coding agents.\n\n"
        "Tools are organized by namespace:\n"
        "- tasca.patron.*: Patron identity management\n"
        "- tasca.table.*: Discussion table operations\n"
        "- tasca.seat.*: Seat presence management\n\n"
        "Start with tasca.patron.register to create your patron identity."
    ),
)


# =============================================================================
# Patron Tools
# =============================================================================


# @invar:allow shell_result: MCP tools return serializable primitives, not Result
# @invar:allow shell_pure_logic: Skeleton - will call core services when implemented
@mcp.tool
def patron_register(
    patron_id: str | None = None,
    display_name: str | None = None,
    alias: str | None = None,
    meta: dict | None = None,
) -> dict:
    """Register a new patron (agent identity).

    Args:
        patron_id: Optional UUID for the patron. Auto-generated if omitted.
        display_name: Human-readable name for the patron.
        alias: Optional short alias for mentions.
        meta: Optional metadata dictionary.

    Returns:
        Registered patron details with patron_id and server_ts.

    Raises:
        NotImplementedError: Feature not yet implemented.
    """
    raise NotImplementedError("Patron registration not yet implemented.")


# @invar:allow shell_result: MCP tools return serializable primitives, not Result
# @invar:allow shell_pure_logic: Skeleton - will call core services when implemented
@mcp.tool
def patron_get(patron_id: str) -> dict:
    """Get patron details by ID.

    Args:
        patron_id: UUID of the patron to retrieve.

    Returns:
        Patron details including patron_id, display_name, alias, and meta.

    Raises:
        NotImplementedError: Feature not yet implemented.
    """
    raise NotImplementedError("Patron retrieval not yet implemented.")


# =============================================================================
# Table Tools
# =============================================================================


# @invar:allow shell_result: MCP tools return serializable primitives, not Result
# @invar:allow shell_pure_logic: Skeleton - will call core services when implemented
@mcp.tool
def table_create(
    created_by: str,
    title: str,
    host_ids: list[str] | None = None,
    metadata: dict | None = None,
    policy: dict | None = None,
    board: dict | None = None,
    dedup_id: str | None = None,
) -> dict:
    """Create a new discussion table.

    Args:
        created_by: Patron ID of the table creator.
        title: Title for the discussion table.
        host_ids: Optional list of patron IDs who can control the table.
        metadata: Optional metadata dictionary.
        policy: Optional policy configuration.
        board: Optional board (pins) data.
        dedup_id: Optional deduplication ID for idempotency.

    Returns:
        Created table details including table_id, invite_code, and web_url.

    Raises:
        NotImplementedError: Feature not yet implemented.
    """
    raise NotImplementedError("Table creation not yet implemented.")


# @invar:allow shell_result: MCP tools return serializable primitives, not Result
# @invar:allow shell_pure_logic: Skeleton - will call core services when implemented
@mcp.tool
def table_join(
    invite_code: str,
    patron_id: str | None = None,
    history_limit: int = 10,
    history_max_bytes: int = 65536,
) -> dict:
    """Join a discussion table by invite code.

    Args:
        invite_code: Invite code or tasca:// URL for the table.
        patron_id: Optional patron ID joining the table.
        history_limit: Maximum number of sayings to fetch (default 10).
        history_max_bytes: Maximum bytes of history to fetch (default 64KB).

    Returns:
        Table details and initial sayings for context.

    Raises:
        NotImplementedError: Feature not yet implemented.
    """
    raise NotImplementedError("Table join not yet implemented.")


# @invar:allow shell_result: MCP tools return serializable primitives, not Result
# @invar:allow shell_pure_logic: Skeleton - will call core services when implemented
@mcp.tool
def table_get(table_id: str) -> dict:
    """Get table details by ID.

    Args:
        table_id: UUID of the table to retrieve.

    Returns:
        Table details including status, version, title, hosts, and metadata.

    Raises:
        NotImplementedError: Feature not yet implemented.
    """
    raise NotImplementedError("Table retrieval not yet implemented.")


# @invar:allow shell_result: MCP tools return serializable primitives, not Result
# @invar:allow shell_pure_logic: Skeleton - will call core services when implemented
@mcp.tool
def table_say(
    table_id: str,
    content: str,
    patron_id: str | None = None,
    speaker_kind: str = "agent",
    saying_type: str = "text",
    mentions: list[str] | None = None,
    reply_to_sequence: int | None = None,
    dedup_id: str | None = None,
) -> dict:
    """Append a saying (message) to a table.

    Args:
        table_id: UUID of the table.
        content: Text content of the saying.
        patron_id: Patron ID of the speaker (required if speaker_kind is 'agent').
        speaker_kind: 'agent' or 'human' (default 'agent').
        saying_type: Type of saying (default 'text').
        mentions: Optional list of patron IDs or 'all' to mention.
        reply_to_sequence: Optional sequence number being replied to.
        dedup_id: Optional deduplication ID for idempotency.

    Returns:
        Saying details including saying_id, sequence, and created_at.

    Raises:
        NotImplementedError: Feature not yet implemented.
    """
    raise NotImplementedError("Table say not yet implemented.")


# @invar:allow shell_result: MCP tools return serializable primitives, not Result
# @invar:allow shell_pure_logic: Skeleton - will call core services when implemented
@mcp.tool
def table_listen(
    table_id: str,
    since_sequence: int = 0,
    limit: int = 50,
    include_table: bool = True,
) -> dict:
    """Listen for new sayings on a table.

    Args:
        table_id: UUID of the table.
        since_sequence: Get sayings after this sequence (exclusive).
        limit: Maximum number of sayings to return (default 50).
        include_table: Include table snapshot in response (default True).

    Returns:
        List of sayings and next_sequence for pagination.

    Raises:
        NotImplementedError: Feature not yet implemented.
    """
    raise NotImplementedError("Table listen not yet implemented.")


# =============================================================================
# Seat Tools
# =============================================================================


# @invar:allow shell_result: MCP tools return serializable primitives, not Result
# @invar:allow shell_pure_logic: Skeleton - will call core services when implemented
@mcp.tool
def seat_heartbeat(
    table_id: str,
    patron_id: str,
    state: str = "running",
    ttl_ms: int = 60000,
) -> dict:
    """Update seat presence on a table.

    Args:
        table_id: UUID of the table.
        patron_id: Patron ID updating their presence.
        state: Seat state - 'running', 'idle', or 'done' (default 'running').
        ttl_ms: Time-to-live in milliseconds (default 60000 = 60s).

    Returns:
        Seat details including expires_at timestamp.

    Raises:
        NotImplementedError: Feature not yet implemented.
    """
    raise NotImplementedError("Seat heartbeat not yet implemented.")


# @invar:allow shell_result: MCP tools return serializable primitives, not Result
# @invar:allow shell_pure_logic: Skeleton - will call core services when implemented
@mcp.tool
def seat_list(table_id: str) -> dict:
    """List all seats (presences) on a table.

    Args:
        table_id: UUID of the table.

    Returns:
        List of active seats with patron_id, state, and timestamps.

    Raises:
        NotImplementedError: Feature not yet implemented.
    """
    raise NotImplementedError("Seat list not yet implemented.")


# =============================================================================
# Server Entry Point
# =============================================================================


# @invar:allow shell_result: Entry point - no return value needed
# @shell_orchestration: Server startup is orchestration, not business logic
def run_mcp_server(transport: TransportType = "stdio") -> None:
    """Run the MCP server.

    Args:
        transport: Transport protocol ('stdio', 'http', 'sse').
    """
    mcp.run(transport=transport)
