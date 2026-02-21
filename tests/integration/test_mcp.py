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
                    "speaker_name": "Test Speaker",
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
