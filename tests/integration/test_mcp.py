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
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest

from tests.integration.harness import MCPSTDIOHarness

if TYPE_CHECKING:
    from tests.integration.conftest import MCPSession


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
# Full-Cycle Test Helpers
# =============================================================================


def _extract_tool_result_strict(response: dict) -> dict:
    """Extract tool result from MCP response, raising on any error.

    Used in happy-path tests where MCP errors are unexpected.

    Args:
        response: Parsed MCP JSON-RPC response dict.

    Returns:
        Parsed content payload dict.

    Raises:
        AssertionError: If the response contains an MCP error or empty content.
    """
    if "error" in response:
        raise AssertionError(f"MCP error: {response['error']}")
    content = response.get("result", {}).get("content", [])
    if not content:
        raise AssertionError("Empty content in response")
    return json.loads(content[0].get("text", "{}"))


def _extract_tool_result_lenient(response: dict) -> dict:
    """Extract tool result from MCP response, returning error dict on failure.

    Used in error-path tests where MCP errors are part of the expected behavior.

    Args:
        response: Parsed MCP JSON-RPC response dict.

    Returns:
        Parsed content payload dict, or ``{"ok": False, "error": ...}`` on failure.
    """
    if "error" in response:
        return {"ok": False, "error": response["error"]}
    content = response.get("result", {}).get("content", [])
    if not content:
        return {"ok": False, "error": "Empty content"}
    return json.loads(content[0].get("text", "{}"))


def make_call_tool(
    session: "MCPSession",
    request_counter: list[int],
    extract_fn: Callable[[dict], dict],
) -> Callable[[str, dict], dict]:
    """Return a stateful ``call_tool`` helper bound to a session and counter.

    Args:
        session: Initialized MCP session providing ``client`` and ``headers``.
        request_counter: Single-element list used as a mutable integer counter.
            Pass ``[1]`` to start; the counter is incremented before each call.
        extract_fn: Result extractor — either ``_extract_tool_result_strict``
            or ``_extract_tool_result_lenient``.

    Returns:
        Callable ``call_tool(name, arguments) -> dict`` ready for use in tests.
    """

    def call_tool(name: str, arguments: dict) -> dict:
        request_counter[0] += 1
        response = session["client"].post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": request_counter[0],
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers=session["headers"],
        )
        assert response.status_code == 200
        return extract_fn(_parse_sse_response(response.text))

    return call_tool


# =============================================================================
# MCP Protocol Tests (HTTP Transport via TestClient)
# =============================================================================


def test_mcp_auth_no_token_returns_401(mcp_test_client) -> None:
    """Test MCP HTTP endpoint returns 401 when no Bearer token is provided.

    Scenario: Unauthenticated MCP Request
    admin_token is always set via fixture_admin_token, so requests without
    an Authorization header must return 401 Unauthorized.
    """
    response = mcp_test_client.post(
        "/mcp",
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

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing or invalid Authorization header"


def test_mcp_auth_invalid_token_returns_401(mcp_test_client) -> None:
    """Test MCP HTTP endpoint returns 401 when invalid Bearer token is provided.

    Scenario: Invalid Bearer Token
    admin_token is always set via fixture_admin_token, so requests with a
    wrong Bearer token must return 401 Unauthorized.
    """
    response = mcp_test_client.post(
        "/mcp",
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
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer invalid-wrong-token",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing token"


def test_mcp_auth_valid_token_succeeds(mcp_test_client) -> None:
    """Test MCP HTTP endpoint succeeds with valid Bearer token.

    Scenario: Valid Bearer Token
    Requests with the fixture Bearer token must proceed to the MCP handler
    and return a valid MCP JSON-RPC response.
    """
    from tests.integration.conftest import TEST_ADMIN_TOKEN

    response = mcp_test_client.post(
        "/mcp",
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
        headers={
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {TEST_ADMIN_TOKEN}",
        },
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data
    assert "protocolVersion" in data["result"]


def test_mcp_initialize(mcp_test_client) -> None:
    """Test MCP initialize request.

    Scenario: MCP Protocol Initialization
    Verifies that the MCP server responds to initialize request
    with server capabilities and protocol version.
    """
    from tests.integration.conftest import TEST_ADMIN_TOKEN

    # admin_token is always set via fixture_admin_token, so auth header is required.
    headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {TEST_ADMIN_TOKEN}",
    }

    response = mcp_test_client.post(
        "/mcp",
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
        headers=headers,
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)

    assert "result" in data
    if "result" in data:
        result = data["result"]
        assert "protocolVersion" in result
        assert "serverInfo" in result


def test_mcp_list_tools(mcp_session: "MCPSession") -> None:
    """Test MCP tools/list request.

    Scenario: MCP Tool Discovery
    Verifies that the MCP server lists available tools.
    Expected tools: patron_register, patron_get, table_create, table_join,
    table_get, table_say, table_listen, seat_heartbeat, seat_list.
    """
    # List tools
    response = mcp_session["client"].post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        },
        headers=mcp_session["headers"],
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)

    assert "result" in data
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
            "table_list",
            "table_say",
            "table_listen",
            "table_control",
            "table_update",
            "table_wait",
            "seat_heartbeat",
            "seat_list",
        }

        assert expected_tools <= tool_names, f"Missing tools: {expected_tools - tool_names}"


# =============================================================================
# Patron Tool Tests (HTTP Transport via TestClient)
# =============================================================================


def test_mcp_patron_register(mcp_session: "MCPSession") -> None:
    """Test patron_register tool.

    Scenario: MCP Patron Registration
    Verifies that patron registration can be invoked.
    """
    response = mcp_session["client"].post(
        "/mcp",
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
        headers=mcp_session["headers"],
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data


def test_mcp_patron_get(mcp_session: "MCPSession") -> None:
    """Test patron_get tool.

    Scenario: MCP Patron Retrieval
    Verifies that patron retrieval can be invoked.
    """
    response = mcp_session["client"].post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "patron_get",
                "arguments": {"patron_id": "test-patron-001"},
            },
        },
        headers=mcp_session["headers"],
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data


# =============================================================================
# Table Tool Tests (HTTP Transport via TestClient)
# =============================================================================


def test_mcp_table_create(mcp_session: "MCPSession") -> None:
    """Test table_create tool.

    Scenario: MCP Table Creation
    Verifies that table creation can be invoked.
    """
    response = mcp_session["client"].post(
        "/mcp",
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
        headers=mcp_session["headers"],
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data


def test_mcp_table_join(mcp_session: "MCPSession") -> None:
    """Test table_join tool.

    Scenario: MCP Table Join
    Verifies that table join can be invoked.
    """
    response = mcp_session["client"].post(
        "/mcp",
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
        headers=mcp_session["headers"],
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data


def test_mcp_table_get(mcp_session: "MCPSession") -> None:
    """Test table_get tool.

    Scenario: MCP Table Retrieval
    Verifies that table retrieval can be invoked.
    """
    response = mcp_session["client"].post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "table_get",
                "arguments": {"table_id": "test-table-001"},
            },
        },
        headers=mcp_session["headers"],
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data


def test_mcp_table_say(mcp_session: "MCPSession") -> None:
    """Test table_say tool.

    Scenario: MCP Table Saying
    Verifies that adding a saying can be invoked.
    """
    response = mcp_session["client"].post(
        "/mcp",
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
        headers=mcp_session["headers"],
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data


def test_mcp_table_listen(mcp_session: "MCPSession") -> None:
    """Test table_listen tool.

    Scenario: MCP Table Listening
    Verifies that listening for sayings can be invoked.
    """
    response = mcp_session["client"].post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "table_listen",
                "arguments": {"table_id": "test-table-001"},
            },
        },
        headers=mcp_session["headers"],
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data


# =============================================================================
# Seat Tool Tests (HTTP Transport via TestClient)
# =============================================================================


def test_mcp_seat_heartbeat(mcp_session: "MCPSession") -> None:
    """Test seat_heartbeat tool.

    Scenario: MCP Seat Presence Update
    Verifies that seat heartbeat can be invoked.
    """
    response = mcp_session["client"].post(
        "/mcp",
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
        headers=mcp_session["headers"],
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data


def test_mcp_seat_list(mcp_session: "MCPSession") -> None:
    """Test seat_list tool.

    Scenario: MCP Seat Listing
    Verifies that listing seats can be invoked.
    """
    response = mcp_session["client"].post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "seat_list",
                "arguments": {"table_id": "test-table-001"},
            },
        },
        headers=mcp_session["headers"],
    )

    assert response.status_code == 200
    data = _parse_sse_response(response.text)
    assert "result" in data


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

        assert "result" in response
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

        assert "result" in response
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
        assert (
            "result" in response or "error" in response
        )  # smoke-test: intentionally tests error response; STDIO transport test verifies only that the process responds, not that the tool succeeds (table_get with a non-existent ID may return an error)


# =============================================================================
# Full-Cycle Integration Tests
# =============================================================================


def test_mcp_full_cycle_patron_flow(mcp_session: "MCPSession") -> None:
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

    request_counter = [1]
    call_tool = make_call_tool(mcp_session, request_counter, _extract_tool_result_strict)

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


def test_mcp_full_cycle_multiple_patrons(mcp_session: "MCPSession") -> None:
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

    request_counter = [1]
    call_tool = make_call_tool(mcp_session, request_counter, _extract_tool_result_strict)

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
# Error Path Tests (Per MCP Spec v0.1 Section 1.3)
# =============================================================================


def test_mcp_error_table_closed(mcp_session: "MCPSession") -> None:
    """Test TableClosed error - post to closed table is rejected.

    Per spec v0.1 Section 1.1:
    - closed is terminal state
    - Server MUST reject: table.say, table.update, table.control
    - Read operations MUST remain allowed: table.get, table.listen, table.wait

    Scenario: TableClosed Error Path
    Verify that posting to a closed table returns OPERATION_NOT_ALLOWED.
    """

    request_counter = [1]
    call_tool = make_call_tool(mcp_session, request_counter, _extract_tool_result_lenient)

    # Setup: register patron and create table
    patron_result = call_tool("patron_register", {"name": "TableClosedTestAgent", "kind": "agent"})
    assert patron_result.get("ok"), f"patron_register failed: {patron_result}"
    patron_id = patron_result["data"]["id"]

    table_result = call_tool(
        "table_create",
        {"question": "Table closed test", "context": "Testing table_control close"},
    )
    assert table_result.get("ok"), f"table_create failed: {table_result}"
    table_id = table_result["data"]["id"]

    # Step 1: Close the table
    close_result = call_tool(
        "table_control",
        {
            "table_id": table_id,
            "action": "close",
            "speaker_name": "TableClosedTestAgent",
            "patron_id": patron_id,
        },
    )
    assert close_result.get("ok"), f"table_control close failed: {close_result}"
    assert close_result["data"]["table_status"] == "closed"

    # Step 2: Attempt to post - MUST be rejected
    say_result = call_tool(
        "table_say",
        {
            "table_id": table_id,
            "content": "This should fail",
            "speaker_kind": "agent",
            "patron_id": patron_id,
        },
    )
    assert not say_result.get("ok"), "table_say should fail on closed table"

    error = say_result.get("error", {})
    error_code = error.get("code") if isinstance(error, dict) else str(error)
    assert error_code in ("OPERATION_NOT_ALLOWED", "TableClosed", "INVALID_STATE"), (
        f"Expected OPERATION_NOT_ALLOWED/TableClosed, got {error_code}"
    )

    # Step 3: Read operations MUST work
    get_result = call_tool("table_get", {"table_id": table_id})
    assert get_result.get("ok"), f"table_get should work on closed table: {get_result}"

    listen_result = call_tool(
        "table_listen",
        {"table_id": table_id, "since_sequence": -1, "limit": 10},
    )
    assert listen_result.get("ok"), f"table_listen should work on closed table: {listen_result}"


def test_mcp_error_dedup_collision(mcp_session: "MCPSession") -> None:
    """Test dedup_id collision returns same response (idempotency).

    Per spec v0.1 Section 3:
    - Dedup scope: per {table_id, speaker_key, tool_name, dedup_id}
    - Behavior: return_existing - return original successful response

    Scenario: dedup_id Idempotency
    Verify that duplicate dedup_id returns same saying_id and sequence.
    """
    import uuid

    request_counter = [1]
    call_tool = make_call_tool(mcp_session, request_counter, _extract_tool_result_lenient)

    # Setup
    patron_result = call_tool("patron_register", {"name": "DedupTestAgent", "kind": "agent"})
    assert patron_result.get("ok")
    patron_id = patron_result["data"]["id"]

    table_result = call_tool(
        "table_create",
        {"question": "Dedup test table", "context": "Testing idempotency"},
    )
    assert table_result.get("ok")
    table_id = table_result["data"]["id"]

    dedup_id = f"test-dedup-{uuid.uuid4()}"

    # First write with dedup_id
    first_result = call_tool(
        "table_say",
        {
            "table_id": table_id,
            "content": "First message",
            "speaker_kind": "agent",
            "patron_id": patron_id,
            "dedup_id": dedup_id,
        },
    )
    assert first_result.get("ok"), f"First write failed: {first_result}"
    first_saying_id = first_result["data"]["saying_id"]
    first_sequence = first_result["data"]["sequence"]

    # Second write with SAME dedup_id - should be idempotent
    second_result = call_tool(
        "table_say",
        {
            "table_id": table_id,
            "content": "Different content - should be ignored",
            "speaker_kind": "agent",
            "patron_id": patron_id,
            "dedup_id": dedup_id,  # Same dedup_id
        },
    )
    assert second_result.get("ok"), f"Second write failed: {second_result}"
    second_saying_id = second_result["data"]["saying_id"]
    second_sequence = second_result["data"]["sequence"]

    # Verify idempotency
    assert first_saying_id == second_saying_id, (
        f"Dedup should return same saying_id: {first_saying_id} != {second_saying_id}"
    )
    assert first_sequence == second_sequence, (
        f"Dedup should return same sequence: {first_sequence} != {second_sequence}"
    )


def test_mcp_error_paused_table(mcp_session: "MCPSession") -> None:
    """Test PAUSED table behavior per spec v0.1 Section 1.1.

    Per spec:
    - paused -> open via table.control(action="resume")
    - For paused state: Server MAY accept table.say (soft enforcement in v0.1)
    - Read operations MUST work

    Scenario: PAUSED Table Behavior
    Verify that read operations work while paused, and resume restores open state.
    """

    request_counter = [1]
    call_tool = make_call_tool(mcp_session, request_counter, _extract_tool_result_lenient)

    # Setup
    patron_result = call_tool("patron_register", {"name": "PausedTestAgent", "kind": "agent"})
    assert patron_result.get("ok")
    patron_id = patron_result["data"]["id"]

    table_result = call_tool(
        "table_create",
        {"question": "Paused test table", "context": "Testing pause/resume"},
    )
    assert table_result.get("ok")
    table_id = table_result["data"]["id"]

    # Step 1: Pause the table
    pause_result = call_tool(
        "table_control",
        {
            "table_id": table_id,
            "action": "pause",
            "speaker_name": "PausedTestAgent",
            "patron_id": patron_id,
        },
    )
    assert pause_result.get("ok"), f"table_control pause failed: {pause_result}"
    assert pause_result["data"]["table_status"] == "paused"

    # Step 2: Verify table status
    get_result = call_tool("table_get", {"table_id": table_id})
    assert get_result.get("ok")
    assert get_result["data"]["status"] == "paused"

    # Step 3: Read operations MUST work while paused
    listen_result = call_tool(
        "table_listen",
        {"table_id": table_id, "since_sequence": -1, "limit": 10},
    )
    assert listen_result.get("ok"), f"table_listen should work while paused: {listen_result}"

    # Step 4: Resume the table
    resume_result = call_tool(
        "table_control",
        {
            "table_id": table_id,
            "action": "resume",
            "speaker_name": "PausedTestAgent",
            "patron_id": patron_id,
        },
    )
    assert resume_result.get("ok"), f"table_control resume failed: {resume_result}"
    assert resume_result["data"]["table_status"] == "open"

    # Step 5: Verify posting works after resume
    say_result = call_tool(
        "table_say",
        {
            "table_id": table_id,
            "content": "Message after resume",
            "speaker_kind": "agent",
            "patron_id": patron_id,
        },
    )
    assert say_result.get("ok"), f"table_say should work after resume: {say_result}"


def test_mcp_error_version_conflict(mcp_session: "MCPSession") -> None:
    """Test VersionConflict error for optimistic concurrency.

    Per spec v0.1 Section 5.2:
    - table_update requires expected_version
    - Returns VersionConflict if version mismatch

    Scenario: VersionConflict Error Path
    Verify that stale version number triggers VersionConflict error.
    """

    request_counter = [1]
    call_tool = make_call_tool(mcp_session, request_counter, _extract_tool_result_lenient)

    # Setup
    patron_result = call_tool("patron_register", {"name": "VersionTestAgent", "kind": "agent"})
    assert patron_result.get("ok")
    patron_id = patron_result["data"]["id"]

    table_result = call_tool(
        "table_create",
        {"question": "Version conflict test", "context": "Testing optimistic concurrency"},
    )
    assert table_result.get("ok")
    table_id = table_result["data"]["id"]

    # Get current version
    get_result = call_tool("table_get", {"table_id": table_id})
    assert get_result.get("ok")
    current_version = get_result["data"]["version"]

    # First update succeeds
    first_update = call_tool(
        "table_update",
        {
            "table_id": table_id,
            "expected_version": current_version,
            "patch": {"question": "Updated question"},
            "speaker_name": "VersionTestAgent",
            "patron_id": patron_id,
        },
    )
    assert first_update.get("ok"), f"First update failed: {first_update}"

    # Second update with stale version - should fail
    stale_update = call_tool(
        "table_update",
        {
            "table_id": table_id,
            "expected_version": current_version,  # Stale!
            "patch": {"question": "Should fail"},
            "speaker_name": "VersionTestAgent",
            "patron_id": patron_id,
        },
    )
    assert not stale_update.get("ok"), "Update with stale version should fail"

    error = stale_update.get("error", {})
    error_code = error.get("code") if isinstance(error, dict) else str(error)
    assert error_code in ("VersionConflict", "VERSION_CONFLICT"), (
        f"Expected VersionConflict, got {error_code}"
    )


def test_mcp_error_invalid_request(mcp_session: "MCPSession") -> None:
    """Test invalid requests return appropriate error codes.

    Per spec Section 1.3:
    - INVALID_REQUEST for malformed input (400)

    Scenario: Invalid Request Error Paths
    Verify that missing required fields trigger appropriate errors.
    """

    request_counter = [1]
    call_tool = make_call_tool(mcp_session, request_counter, _extract_tool_result_lenient)

    # Setup
    patron_result = call_tool("patron_register", {"name": "InvalidReqAgent", "kind": "agent"})
    assert patron_result.get("ok")
    patron_id = patron_result["data"]["id"]

    table_result = call_tool(
        "table_create",
        {"question": "Invalid request test", "context": "Testing validation"},
    )
    assert table_result.get("ok")
    table_id = table_result["data"]["id"]

    # Test: table_join with neither table_id nor invite_code
    join_result = call_tool(
        "table_join",
        {"patron_id": patron_id},  # Neither table_id nor invite_code
    )
    assert not join_result.get("ok"), "table_join should fail without table identifier"

    error = join_result.get("error", {})
    error_code = error.get("code") if isinstance(error, dict) else str(error)
    assert error_code in ("INVALID_REQUEST", "NOT_FOUND"), (
        f"Expected INVALID_REQUEST or NOT_FOUND, got {error_code}"
    )

    # Test: table_say with human speaker but patron_id provided
    human_result = call_tool(
        "table_say",
        {
            "table_id": table_id,
            "content": "Human with patron_id",
            "speaker_kind": "human",
            "patron_id": patron_id,  # Should NOT have patron_id for human
        },
    )
    # This should fail per spec
    assert not human_result.get("ok"), "table_say should reject patron_id for human speaker"


# =============================================================================
# Batch Delete Integration Tests
# =============================================================================


def test_mcp_batch_delete_full_cycle(mcp_session: "MCPSession") -> None:
    """Test full batch delete cycle: create tables → close → batch delete.

    Scenario: Full Batch Delete Lifecycle
    1. Create two tables
    2. Close both tables via table_control
    3. Verify both are closed
    4. Batch delete both
    5. Verify tables are gone
    """
    request_counter = [1]
    call_tool = make_call_tool(mcp_session, request_counter, _extract_tool_result_strict)

    # 1. Create two tables
    t1 = call_tool("table_create", {"question": "Batch delete test 1"})
    assert t1.get("ok") is True
    t1_id = t1["data"]["id"]

    t2 = call_tool("table_create", {"question": "Batch delete test 2"})
    assert t2.get("ok") is True
    t2_id = t2["data"]["id"]

    # 2. Close both tables
    close1 = call_tool(
        "table_control",
        {"table_id": t1_id, "action": "close", "speaker_name": "test"},
    )
    assert close1.get("ok") is True

    close2 = call_tool(
        "table_control",
        {"table_id": t2_id, "action": "close", "speaker_name": "test"},
    )
    assert close2.get("ok") is True

    # 3. Batch delete both
    delete_result = call_tool("table_delete_batch", {"ids": [t1_id, t2_id]})
    assert delete_result.get("ok") is True, f"batch delete failed: {delete_result}"
    assert set(delete_result["data"]["deleted_ids"]) == {t1_id, t2_id}

    # 4. Verify tables are gone
    call_tool_lenient = make_call_tool(
        mcp_session, request_counter, _extract_tool_result_lenient
    )
    get1 = call_tool_lenient("table_get", {"table_id": t1_id})
    assert get1.get("ok") is False, "Deleted table should not be found"

    get2 = call_tool_lenient("table_get", {"table_id": t2_id})
    assert get2.get("ok") is False, "Deleted table should not be found"


def test_mcp_batch_delete_rejects_open(mcp_session: "MCPSession") -> None:
    """Test batch delete rejects open (non-closed) tables.

    Scenario: Batch Delete Precondition Failure
    Verifies all-or-nothing semantics: if any table is not closed,
    the entire batch is rejected.
    """
    request_counter = [1]
    call_tool = make_call_tool(mcp_session, request_counter, _extract_tool_result_strict)
    call_tool_lenient = make_call_tool(
        mcp_session, request_counter, _extract_tool_result_lenient
    )

    # Create one open table
    t1 = call_tool("table_create", {"question": "Should stay open"})
    assert t1.get("ok") is True
    t1_id = t1["data"]["id"]

    # Try to batch delete the open table
    delete_result = call_tool_lenient("table_delete_batch", {"ids": [t1_id]})
    assert delete_result.get("ok") is False, "Should reject open table"
    assert delete_result["error"]["code"] == "BATCH_PRECONDITION_FAILED"

    # Verify table still exists
    get_result = call_tool("table_get", {"table_id": t1_id})
    assert get_result.get("ok") is True, "Open table should still exist"


def test_mcp_table_list_status_filter(mcp_session: "MCPSession") -> None:
    """Test table_list with different status filters.

    Scenario: Table List Status Filtering
    Verifies that table_list returns correct results for
    open, closed, and all status filters.
    """
    request_counter = [1]
    call_tool = make_call_tool(mcp_session, request_counter, _extract_tool_result_strict)

    # Create a table and close it
    t1 = call_tool("table_create", {"question": "Filter test table"})
    assert t1.get("ok") is True
    t1_id = t1["data"]["id"]

    close1 = call_tool(
        "table_control",
        {"table_id": t1_id, "action": "close", "speaker_name": "test"},
    )
    assert close1.get("ok") is True

    # List closed tables
    closed_list = call_tool("table_list", {"status": "closed"})
    assert closed_list.get("ok") is True
    closed_ids = {t["id"] for t in closed_list["data"]["tables"]}
    assert t1_id in closed_ids, "Closed table should appear in closed list"

    # List all tables
    all_list = call_tool("table_list", {"status": "all"})
    assert all_list.get("ok") is True
    assert all_list["data"]["total"] >= 1

    # List open tables (closed table should not appear)
    open_list = call_tool("table_list", {"status": "open"})
    assert open_list.get("ok") is True
    open_ids = {t["id"] for t in open_list["data"]["tables"]}
    assert t1_id not in open_ids, "Closed table should not appear in open list"


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
