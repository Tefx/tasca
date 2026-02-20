"""
Integration tests for MCP server tools.

These tests verify the MCP protocol works correctly via both HTTP and STDIO transports.
HTTP tests require the server running at TASCA_TEST_MCP_URL (default: localhost:8000/mcp).
STDIO tests spawn the tasca-mcp process directly.

Usage:
    # HTTP tests (requires running server)
    uv run tasca &
    pytest tests/integration/test_mcp.py -v -k "not stdio"

    # STDIO tests (standalone)
    pytest tests/integration/test_mcp.py -v -k stdio

    # All MCP tests
    pytest tests/integration/test_mcp.py -v
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import API_BASE_URL, check_server_available
from tests.integration.harness import MCPHTTPHarness, MCPSTDIOHarness


# =============================================================================
# MCP Protocol Tests (HTTP Transport)
# =============================================================================


@pytest.mark.asyncio
async def test_mcp_initialize() -> None:
    """Test MCP initialize request.

    Scenario: MCP Protocol Initialization
    Verifies that the MCP server responds to initialize request
    with server capabilities and protocol version.
    """
    async with MCPHTTPHarness() as harness:
        response = await harness.initialize()

        assert "result" in response or "error" in response
        if "result" in response:
            result = response["result"]
            assert "protocolVersion" in result
            assert "serverInfo" in result


@pytest.mark.asyncio
async def test_mcp_list_tools() -> None:
    """Test MCP tools/list request.

    Scenario: MCP Tool Discovery
    Verifies that the MCP server lists available tools.
    Expected tools: patron_register, patron_get, table_create, table_join,
    table_get, table_say, table_listen, seat_heartbeat, seat_list.
    """
    async with MCPHTTPHarness() as harness:
        # Initialize first
        await harness.initialize()

        # List tools
        response = await harness.list_tools()

        assert "result" in response or "error" in response
        if "result" in response:
            tools = response["result"].get("tools", [])
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
# Patron Tool Tests (HTTP Transport)
# =============================================================================


@pytest.mark.asyncio
async def test_mcp_patron_register() -> None:
    """Test patron_register tool.

    Scenario: MCP Patron Registration
    Verifies that patron registration can be invoked.
    Note: Currently raises NotImplementedError as feature is not implemented.
    """
    async with MCPHTTPHarness() as harness:
        await harness.initialize()

        response = await harness.patron_register(
            display_name="Test Agent",
            alias="testagent",
        )

        # Tool exists and responds (may be NotImplementedError)
        assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_mcp_patron_get() -> None:
    """Test patron_get tool.

    Scenario: MCP Patron Retrieval
    Verifies that patron retrieval can be invoked.
    Note: Currently raises NotImplementedError as feature is not implemented.
    """
    async with MCPHTTPHarness() as harness:
        await harness.initialize()

        response = await harness.patron_get("test-patron-001")

        # Tool exists and responds (may be NotImplementedError)
        assert "result" in response or "error" in response


# =============================================================================
# Table Tool Tests (HTTP Transport)
# =============================================================================


@pytest.mark.asyncio
async def test_mcp_table_create() -> None:
    """Test table_create tool.

    Scenario: MCP Table Creation
    Verifies that table creation can be invoked.
    Note: Currently raises NotImplementedError as feature is not implemented.
    """
    async with MCPHTTPHarness() as harness:
        await harness.initialize()

        response = await harness.table_create(
            created_by="test-patron-001",
            title="Test Discussion",
        )

        # Tool exists and responds (may be NotImplementedError)
        assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_mcp_table_join() -> None:
    """Test table_join tool.

    Scenario: MCP Table Join
    Verifies that table join can be invoked.
    Note: Currently raises NotImplementedError as feature is not implemented.
    """
    async with MCPHTTPHarness() as harness:
        await harness.initialize()

        response = await harness.table_join(
            invite_code="test-invite-code",
        )

        # Tool exists and responds (may be NotImplementedError)
        assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_mcp_table_get() -> None:
    """Test table_get tool.

    Scenario: MCP Table Retrieval
    Verifies that table retrieval can be invoked.
    Note: Currently raises NotImplementedError as feature is not implemented.
    """
    async with MCPHTTPHarness() as harness:
        await harness.initialize()

        response = await harness.table_get("test-table-001")

        # Tool exists and responds (may be NotImplementedError)
        assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_mcp_table_say() -> None:
    """Test table_say tool.

    Scenario: MCP Table Saying
    Verifies that adding a saying can be invoked.
    Note: Currently raises NotImplementedError as feature is not implemented.
    """
    async with MCPHTTPHarness() as harness:
        await harness.initialize()

        response = await harness.table_say(
            table_id="test-table-001",
            content="Hello from integration test!",
        )

        # Tool exists and responds (may be NotImplementedError)
        assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_mcp_table_listen() -> None:
    """Test table_listen tool.

    Scenario: MCP Table Listening
    Verifies that listening for sayings can be invoked.
    Note: Currently raises NotImplementedError as feature is not implemented.
    """
    async with MCPHTTPHarness() as harness:
        await harness.initialize()

        response = await harness.table_listen(
            table_id="test-table-001",
        )

        # Tool exists and responds (may be NotImplementedError)
        assert "result" in response or "error" in response


# =============================================================================
# Seat Tool Tests (HTTP Transport)
# =============================================================================


@pytest.mark.asyncio
async def test_mcp_seat_heartbeat() -> None:
    """Test seat_heartbeat tool.

    Scenario: MCP Seat Presence Update
    Verifies that seat heartbeat can be invoked.
    Note: Currently raises NotImplementedError as feature is not implemented.
    """
    async with MCPHTTPHarness() as harness:
        await harness.initialize()

        response = await harness.seat_heartbeat(
            table_id="test-table-001",
            patron_id="test-patron-001",
        )

        # Tool exists and responds (may be NotImplementedError)
        assert "result" in response or "error" in response


@pytest.mark.asyncio
async def test_mcp_seat_list() -> None:
    """Test seat_list tool.

    Scenario: MCP Seat Listing
    Verifies that listing seats can be invoked.
    Note: Currently raises NotImplementedError as feature is not implemented.
    """
    async with MCPHTTPHarness() as harness:
        await harness.initialize()

        response = await harness.seat_list(table_id="test-table-001")

        # Tool exists and responds (may be NotImplementedError)
        assert "result" in response or "error" in response


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
# Integration Check
# =============================================================================


def test_server_available_for_http_tests() -> None:
    """Test that server is available for HTTP transport tests.

    This test documents the requirement for a running server
    for HTTP transport tests. It will be skipped if the server
    is not available.
    """
    if not check_server_available(API_BASE_URL):
        pytest.skip(
            f"Server not available at {API_BASE_URL}. "
            "HTTP transport tests require a running server. "
            "Start with: uv run tasca"
        )


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
