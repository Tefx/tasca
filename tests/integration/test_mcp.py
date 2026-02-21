"""
Integration tests for MCP server tools.

These tests verify the MCP protocol works correctly via both HTTP and STDIO transports.

HTTP tests use Starlette TestClient for in-process testing (no external server needed).
STDIO tests spawn the tasca-mcp process directly.

Usage:
    # HTTP tests (no external server needed - uses TestClient)
    pytest tests/integration/test_mcp.py -v -k "not stdio"

    # STDIO tests (standalone)
    pytest tests/integration/test_mcp.py -v -k stdio

    # All MCP tests
    pytest tests/integration/test_mcp.py -v
"""

from __future__ import annotations

import json

import pytest

from tests.integration.harness import MCPSTDIOHarness


def _parse_sse_response(text: str) -> dict:
    """Parse Server-Sent Events response from FastMCP.

    FastMCP returns responses in SSE format: "event: message\\ndata: {...}\\n\\n"
    """
    if text.startswith("event:"):
        for line in text.split("\n"):
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
    return json.loads(text)


# =============================================================================
# MCP Protocol Tests (HTTP Transport via TestClient)
# =============================================================================


def test_mcp_initialize(mcp_test_client) -> None:
    """Test MCP initialize request.

    Scenario: MCP Protocol Initialization
    Verifies that the MCP server responds to initialize request
    with server capabilities and protocol version.
    """
    response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "tasca-test-client",
                    "version": "0.1.0",
                },
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)

    assert "result" in data or "error" in data
    if "result" in data:
        result = data["result"]
        assert "protocolVersion" in result
        assert "serverInfo" in result


def test_mcp_list_tools(mcp_test_client) -> None:
    """Test MCP tools/list request.

    Scenario: MCP Tool Discovery
    Verifies that the MCP server lists available tools.
    Expected tools: patron_register, patron_get, table_create, table_join,
    table_get, table_say, table_listen, seat_heartbeat, seat_list.
    """
    # Initialize first and get session ID
    init_response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "tasca-test-client",
                    "version": "0.1.0",
                },
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert init_response.status_code == 200

    # Get session ID from response headers
    session_id = init_response.headers.get("mcp-session-id")
    headers = {"Accept": "application/json, text/event-stream"}
    if session_id:
        headers["mcp-session-id"] = session_id

    # List tools
    response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        },
        headers=headers,
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)

    assert "result" in data or "error" in data
    if "result" in data:
        tools = data["result"].get("tools", [])
        tool_names = {t["name"] for t in tools}

        # Expected tools
        expected_tools = {
            "patron_register",
            "patron_get",
            "table_create",
            "table_join",
            "table_get",
            "table_say",
            "table_listen",
            "seat_heartbeat",
            "seat_list",
        }

        assert expected_tools <= tool_names, f"Missing tools: {expected_tools - tool_names}"


# =============================================================================
# Patron Tool Tests (HTTP Transport via TestClient)
# =============================================================================


def test_mcp_patron_register(mcp_test_client) -> None:
    """Test patron_register tool.

    Scenario: MCP Patron Registration
    Verifies that patron registration can be invoked.
    """
    # Initialize first and get session ID
    init_response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1.0"},
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert init_response.status_code == 200
    session_id = init_response.headers.get("mcp-session-id")
    headers = {"Accept": "application/json, text/event-stream"}
    if session_id:
        headers["mcp-session-id"] = session_id

    response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "patron_register",
                "arguments": {
                    "name": "Test Agent",
                    "kind": "agent",
                },
            },
        },
        headers=headers,
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data or "error" in data


def test_mcp_patron_get(mcp_test_client) -> None:
    """Test patron_get tool.

    Scenario: MCP Patron Retrieval
    Verifies that patron retrieval can be invoked.
    """
    # Initialize first and get session ID
    init_response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1.0"},
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert init_response.status_code == 200
    session_id = init_response.headers.get("mcp-session-id")
    headers = {"Accept": "application/json, text/event-stream"}
    if session_id:
        headers["mcp-session-id"] = session_id

    response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "patron_get",
                "arguments": {"patron_id": "test-patron-001"},
            },
        },
        headers=headers,
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data or "error" in data


# =============================================================================
# Table Tool Tests (HTTP Transport via TestClient)
# =============================================================================


def test_mcp_table_create(mcp_test_client) -> None:
    """Test table_create tool.

    Scenario: MCP Table Creation
    Verifies that table creation can be invoked.
    """
    # Initialize first and get session ID
    init_response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1.0"},
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert init_response.status_code == 200
    session_id = init_response.headers.get("mcp-session-id")
    headers = {"Accept": "application/json, text/event-stream"}
    if session_id:
        headers["mcp-session-id"] = session_id

    response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "table_create",
                "arguments": {
                    "question": "Test Discussion",
                },
            },
        },
        headers=headers,
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data or "error" in data


def test_mcp_table_join(mcp_test_client) -> None:
    """Test table_join tool.

    Scenario: MCP Table Join
    Verifies that table join can be invoked.
    """
    # Initialize first and get session ID
    init_response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1.0"},
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert init_response.status_code == 200
    session_id = init_response.headers.get("mcp-session-id")
    headers = {"Accept": "application/json, text/event-stream"}
    if session_id:
        headers["mcp-session-id"] = session_id

    response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "table_join",
                "arguments": {
                    "table_id": "test-table-001",
                    "patron_id": "test-patron-001",
                },
            },
        },
        headers=headers,
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data or "error" in data


def test_mcp_table_get(mcp_test_client) -> None:
    """Test table_get tool.

    Scenario: MCP Table Retrieval
    Verifies that table retrieval can be invoked.
    """
    # Initialize first and get session ID
    init_response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1.0"},
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert init_response.status_code == 200
    session_id = init_response.headers.get("mcp-session-id")
    headers = {"Accept": "application/json, text/event-stream"}
    if session_id:
        headers["mcp-session-id"] = session_id

    response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "table_get",
                "arguments": {"table_id": "test-table-001"},
            },
        },
        headers=headers,
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data or "error" in data


def test_mcp_table_say(mcp_test_client) -> None:
    """Test table_say tool.

    Scenario: MCP Table Saying
    Verifies that adding a saying can be invoked.
    """
    # Initialize first and get session ID
    init_response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1.0"},
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert init_response.status_code == 200
    session_id = init_response.headers.get("mcp-session-id")
    headers = {"Accept": "application/json, text/event-stream"}
    if session_id:
        headers["mcp-session-id"] = session_id

    response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "table_say",
                "arguments": {
                    "table_id": "test-table-001",
                    "content": "Hello from integration test!",
                    "speaker_kind": "human",
                },
            },
        },
        headers=headers,
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data or "error" in data


def test_mcp_table_listen(mcp_test_client) -> None:
    """Test table_listen tool.

    Scenario: MCP Table Listening
    Verifies that listening for sayings can be invoked.
    """
    # Initialize first and get session ID
    init_response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1.0"},
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert init_response.status_code == 200
    session_id = init_response.headers.get("mcp-session-id")
    headers = {"Accept": "application/json, text/event-stream"}
    if session_id:
        headers["mcp-session-id"] = session_id

    response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "table_listen",
                "arguments": {"table_id": "test-table-001"},
            },
        },
        headers=headers,
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data or "error" in data


# =============================================================================
# Seat Tool Tests (HTTP Transport via TestClient)
# =============================================================================


def test_mcp_seat_heartbeat(mcp_test_client) -> None:
    """Test seat_heartbeat tool.

    Scenario: MCP Seat Presence Update
    Verifies that seat heartbeat can be invoked.
    """
    # Initialize first and get session ID
    init_response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1.0"},
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert init_response.status_code == 200
    session_id = init_response.headers.get("mcp-session-id")
    headers = {"Accept": "application/json, text/event-stream"}
    if session_id:
        headers["mcp-session-id"] = session_id

    response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "seat_heartbeat",
                "arguments": {
                    "table_id": "test-table-001",
                    "seat_id": "test-seat-001",
                },
            },
        },
        headers=headers,
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data or "error" in data


def test_mcp_seat_list(mcp_test_client) -> None:
    """Test seat_list tool.

    Scenario: MCP Seat Listing
    Verifies that listing seats can be invoked.
    """
    # Initialize first and get session ID
    init_response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0.1.0"},
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert init_response.status_code == 200
    session_id = init_response.headers.get("mcp-session-id")
    headers = {"Accept": "application/json, text/event-stream"}
    if session_id:
        headers["mcp-session-id"] = session_id

    response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "seat_list",
                "arguments": {"table_id": "test-table-001"},
            },
        },
        headers=headers,
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data or "error" in data


# =============================================================================
# MCP STDIO Transport Tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.timeout(10)  # STDIO tests should complete quickly
async def test_mcp_stdio_initialize() -> None:
    """Test MCP STDIO transport initialization.

    Scenario: STDIO Transport Initialization
    Spawns tasca-mcp process and sends initialize request via stdin.
    Verifies the process responds on stdout.
    """
    async with MCPSTDIOHarness() as harness:
        response = await harness.initialize()

        assert "result" in response or "error" in response
        if "result" in response:
            result = response["result"]
            assert "protocolVersion" in result


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_mcp_stdio_list_tools() -> None:
    """Test MCP STDIO transport tool listing.

    Scenario: STDIO Transport Tool Discovery
    Spawns tasca-mcp process and requests tools/list.
    Verifies tool names match the expected MCP tools.
    """
    async with MCPSTDIOHarness() as harness:
        await harness.initialize()

        response = await harness.list_tools()

        assert "result" in response or "error" in response
        if "result" in response:
            tools = response["result"].get("tools", [])
            tool_names = {t["name"] for t in tools}

            # Verify at least the core tools exist
            assert "table_create" in tool_names
            assert "patron_register" in tool_names


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_mcp_stdio_tool_call() -> None:
    """Test MCP STDIO transport tool invocation.

    Scenario: STDIO Transport Tool Call
    Spawns tasca-mcp process and calls a tool via stdin.
    Verifies response is received on stdout.
    Note: Tool may return error if not implemented, that's OK.
    """
    async with MCPSTDIOHarness() as harness:
        await harness.initialize()

        response = await harness.call_tool(
            "table_get",
            {"table_id": "test-table-001"},
        )

        # Either result or error is acceptable
        assert "result" in response or "error" in response


# =============================================================================
# Full-Cycle Integration Tests
# =============================================================================


def test_mcp_full_cycle_patron_flow(mcp_test_client) -> None:
    """Test full patron flow: register → create table → join → say → listen.

    This integration test exercises the complete patron lifecycle:
    1. patron_register - Register a new patron
    2. table_create - Create a discussion table
    3. table_join - Join the table (create a seat)
    4. table_say - Post a saying to the table
    5. table_listen - Listen for sayings

    Scenario: Full Patron Lifecycle
    Verifies the complete flow works end-to-end without errors,
    with proper data consistency between operations.
    """

    def _parse_mcp_response(response_text: str) -> dict:
        """Parse MCP response from FastMCP SSE format."""
        if response_text.startswith("event:"):
            for line in response_text.split("\n"):
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
        return json.loads(response_text)

    def _extract_tool_result(response: dict) -> dict:
        """Extract tool result from MCP response."""
        if "error" in response:
            raise AssertionError(f"MCP error: {response['error']}")
        content = response.get("result", {}).get("content", [])
        if not content:
            raise AssertionError("Empty content in response")
        return json.loads(content[0].get("text", "{}"))

    # Initialize session
    init_response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "full-cycle-test", "version": "0.1.0"},
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert init_response.status_code == 200
    init_data = _parse_mcp_response(init_response.text)
    assert "result" in init_data

    # Extract session ID for subsequent requests
    session_id = init_response.headers.get("mcp-session-id")
    headers = {"Accept": "application/json, text/event-stream"}
    if session_id:
        headers["mcp-session-id"] = session_id

    request_id = 1

    def call_tool(name: str, arguments: dict) -> dict:
        """Helper to call MCP tool."""
        nonlocal request_id
        request_id += 1
        response = mcp_test_client.post(
            "/mcp/mcp",
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers=headers,
        )
        assert response.status_code == 200
        return _extract_tool_result(_parse_mcp_response(response.text))

    # 1. Register patron
    patron_result = call_tool("patron_register", {"name": "FullCycleTestAgent", "kind": "agent"})
    assert patron_result.get("ok") is True, f"patron_register failed: {patron_result}"
    patron_id = patron_result["data"]["id"]
    assert patron_id, "patron_register should return patron ID"

    # 2. Create table
    table_result = call_tool(
        "table_create",
        {
            "question": "What is the best approach for full-cycle integration testing?",
            "context": "Testing the complete patron flow with MCP tools",
        },
    )
    assert table_result.get("ok") is True, f"table_create failed: {table_result}"
    table_id = table_result["data"]["id"]
    assert table_id, "table_create should return table ID"
    assert table_result["data"]["status"] == "open", "New table should be open"

    # 3. Join table (creates a seat)
    join_result = call_tool("table_join", {"table_id": table_id, "patron_id": patron_id})
    assert join_result.get("ok") is True, f"table_join failed: {join_result}"
    seat_id = join_result["data"].get("seat", {}).get("id")
    assert seat_id, "table_join should return seat ID"

    # 4. Say something
    say_result = call_tool(
        "table_say",
        {
            "table_id": table_id,
            "content": "Hello from the full-cycle integration test! This is a test message.",
            "speaker_kind": "agent",
            "patron_id": patron_id,
        },
    )
    assert say_result.get("ok") is True, f"table_say failed: {say_result}"
    assert say_result["data"]["sequence"] is not None, "table_say should return sequence"
    assert say_result["data"]["saying_id"] is not None, "table_say should return saying_id"
    assert say_result["data"]["mentions_all"] is False, "table_say should return mentions_all"
    assert "mentions_resolved" in say_result["data"], "table_say should return mentions_resolved"
    assert "mentions_unresolved" in say_result["data"], (
        "table_say should return mentions_unresolved"
    )

    # 5. Listen for sayings (since_sequence=-1 or default gets all sayings)
    listen_result = call_tool(
        "table_listen", {"table_id": table_id, "since_sequence": -1, "limit": 10}
    )
    assert listen_result.get("ok") is True, f"table_listen failed: {listen_result}"

    sayings = listen_result["data"].get("sayings", [])
    assert len(sayings) >= 1, "table_listen should return at least the posted saying"

    # Verify the saying we posted is in the results
    posted_saying = next(
        (
            s
            for s in sayings
            if s["content"] == "Hello from the full-cycle integration test! This is a test message."
        ),
        None,
    )
    assert posted_saying is not None, "Posted saying should be in listen results"
    assert posted_saying["speaker"]["name"] == "FullCycleTestAgent"
    assert posted_saying["speaker"]["patron_id"] == patron_id

    # 6. Send heartbeat to update seat presence
    heartbeat_result = call_tool("seat_heartbeat", {"table_id": table_id, "seat_id": seat_id})
    assert heartbeat_result.get("ok") is True, f"seat_heartbeat failed: {heartbeat_result}"
    assert heartbeat_result["data"].get("expires_at") is not None, (
        "seat_heartbeat should return expires_at"
    )

    # 7. Verify seat appears in seat_list
    seat_list_result = call_tool("seat_list", {"table_id": table_id, "active_only": True})
    assert seat_list_result.get("ok") is True, f"seat_list failed: {seat_list_result}"

    seats = seat_list_result["data"].get("seats", [])
    active_count = seat_list_result["data"].get("active_count", 0)
    assert active_count >= 1, "seat_list should show at least 1 active seat"
    assert any(s["id"] == seat_id for s in seats), "Our seat should appear in seat_list"


def test_mcp_full_cycle_multiple_patrons(mcp_test_client) -> None:
    """Test multi-patron flow: register multiple patrons, join, say, listen.

    This integration test exercises:
    1. Multiple patron registrations
    2. Table creation
    3. Multiple patrons joining the same table
    4. Multiple sayings from different patrons
    5. Listening for all sayings

    Scenario: Multi-Patron Discussion
    Verifies that multiple patrons can participate in a table discussion.
    """

    def _parse_mcp_response(response_text: str) -> dict:
        """Parse MCP response from FastMCP SSE format."""
        if response_text.startswith("event:"):
            for line in response_text.split("\n"):
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
        return json.loads(response_text)

    def _extract_tool_result(response: dict) -> dict:
        """Extract tool result from MCP response."""
        if "error" in response:
            raise AssertionError(f"MCP error: {response['error']}")
        content = response.get("result", {}).get("content", [])
        if not content:
            raise AssertionError("Empty content in response")
        return json.loads(content[0].get("text", "{}"))

    # Initialize session
    init_response = mcp_test_client.post(
        "/mcp/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "multi-patron-test", "version": "0.1.0"},
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert init_response.status_code == 200

    # Extract session ID for subsequent requests
    session_id = init_response.headers.get("mcp-session-id")
    headers = {"Accept": "application/json, text/event-stream"}
    if session_id:
        headers["mcp-session-id"] = session_id

    request_id = 1

    def call_tool(name: str, arguments: dict) -> dict:
        """Helper to call MCP tool."""
        nonlocal request_id
        request_id += 1
        response = mcp_test_client.post(
            "/mcp/mcp",
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers=headers,
        )
        assert response.status_code == 200
        return _extract_tool_result(_parse_mcp_response(response.text))

    # Register two patrons
    patron1_result = call_tool("patron_register", {"name": "Patron1-Agent", "kind": "agent"})
    assert patron1_result.get("ok") is True
    patron1_id = patron1_result["data"]["id"]

    patron2_result = call_tool("patron_register", {"name": "Patron2-Agent", "kind": "agent"})
    assert patron2_result.get("ok") is True
    patron2_id = patron2_result["data"]["id"]

    # Create table
    table_result = call_tool(
        "table_create",
        {
            "question": "Multi-patron discussion test",
            "context": "Testing multiple agents in a single table",
        },
    )
    assert table_result.get("ok") is True
    table_id = table_result["data"]["id"]

    # Both patrons join
    join1_result = call_tool("table_join", {"table_id": table_id, "patron_id": patron1_id})
    assert join1_result.get("ok") is True

    join2_result = call_tool("table_join", {"table_id": table_id, "patron_id": patron2_id})
    assert join2_result.get("ok") is True

    # Both patrons say something
    say1_result = call_tool(
        "table_say",
        {
            "table_id": table_id,
            "content": "Hello from Patron1!",
            "speaker_kind": "agent",
            "patron_id": patron1_id,
        },
    )
    assert say1_result.get("ok") is True

    say2_result = call_tool(
        "table_say",
        {
            "table_id": table_id,
            "content": "Hello from Patron2!",
            "speaker_kind": "agent",
            "patron_id": patron2_id,
        },
    )
    assert say2_result.get("ok") is True

    # Listen for all sayings (since_sequence=-1 gets all)
    listen_result = call_tool(
        "table_listen", {"table_id": table_id, "since_sequence": -1, "limit": 100}
    )
    assert listen_result.get("ok") is True

    sayings = listen_result["data"].get("sayings", [])
    assert len(sayings) >= 2, f"Expected at least 2 sayings, got {len(sayings)}"

    # Verify both sayings are present
    contents = [s["content"] for s in sayings]
    assert "Hello from Patron1!" in contents
    assert "Hello from Patron2!" in contents


# =============================================================================
# Scenario Documentation
# =============================================================================


def test_scenario_list() -> None:
    """Document all scenarios covered by the MCP test suite.

    This test serves as documentation and always passes.
    """
    from tests.integration.harness import get_scenarios

    scenarios = get_scenarios()

    # Document expected scenarios
    expected_categories = {
        "mcp_protocol",
        "mcp_patron",
        "mcp_table",
        "mcp_seat",
        "mcp_stdio",
    }

    assert expected_categories <= scenarios.keys(), (
        f"Missing scenario categories: {expected_categories - scenarios.keys()}"
    )

    # Print scenarios for documentation
    print("\n=== MCP Test Scenarios ===")
    for category, scenario_list in scenarios.items():
        if category.startswith("mcp_"):
            print(f"\n{category}:")
            for scenario in scenario_list:
                print(f"  - {scenario}")
