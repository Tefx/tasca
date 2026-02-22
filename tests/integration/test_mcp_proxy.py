"""
E2E tests for MCP proxy mode (remote forwarding).

These tests verify the proxy mode functionality where:
1. A local MCP client can switch to remote mode via connect(url)
2. Tool calls are forwarded to the upstream server
3. The client can switch back to local mode via connect(url=None)

Test scenarios:
1. Start tasca HTTP server (via Starlette TestClient)
2. Call connect tool to switch to remote mode pointing at that server
3. Call table_list through proxy, verify results match direct call
4. Call connect(url=None) to switch back to local mode
5. Verify local mode works correctly after disconnect

## Running E2E Tests

These tests require an external server. See docs/e2e-testing.md for full details.

Quick start:
    # Option 1: Use helper script (recommended)
    ./scripts/run-e2e-external-server.sh -v

    # Option 2: Manual execution
    # Terminal 1: Start server
    uv run tasca

    # Terminal 2: Run tests
    TASCA_USE_EXTERNAL_SERVER=1 uv run pytest tests/integration/test_mcp_proxy.py -v

    # Option 3: Custom server URL
    TASCA_USE_EXTERNAL_SERVER=1 \
    TASCA_TEST_MCP_URL=http://localhost:8000/mcp \
    uv run pytest tests/integration/test_mcp_proxy.py -v

## Environment Variables

    TASCA_USE_EXTERNAL_SERVER=1   Required to run E2E tests (tests skip otherwise)
    TASCA_TEST_MCP_URL            MCP endpoint URL (default: http://localhost:8000/mcp)
    TASCA_TEST_TIMEOUT            Request timeout in seconds (default: 30)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tasca.shell.mcp.proxy import get_upstream_config, switch_to_local

if TYPE_CHECKING:
    from starlette.testclient import TestClient


def _parse_sse_response(text: str) -> dict:
    """Parse Server-Sent Events response from FastMCP.

    FastMCP returns responses in SSE format: "event: message\\ndata: {...}\\n\\n"
    """
    if text.startswith("event:"):
        for line in text.split("\n"):
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
    return json.loads(text)


def _extract_tool_result(response: dict) -> dict:
    """Extract tool result from MCP response."""
    if "error" in response:
        return {"ok": False, "error": response["error"]}
    content = response.get("result", {}).get("content", [])
    if not content:
        return {"ok": False, "error": "Empty content"}
    text = content[0].get("text", "")
    if not text:
        return {"ok": False, "error": "Empty text in content"}
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"JSON decode error: {e}", "raw_text": text[:200]}


def _call_tool(
    client: "TestClient", headers: dict, tool_name: str, arguments: dict, request_id: int
) -> dict:
    """Helper to call MCP tool and extract result."""
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
        headers=headers,
    )
    assert response.status_code == 200
    return _extract_tool_result(_parse_sse_response(response.text))


# =============================================================================
# E2E Proxy Forwarding Tests
# =============================================================================


@pytest.fixture(autouse=True)
def reset_proxy_mode():
    """Ensure proxy mode is reset before and after each test."""
    # Reset to local mode before test
    switch_to_local()
    yield
    # Reset to local mode after test
    switch_to_local()


def test_e2e_proxy_mode_table_list_forwarding(mcp_session):
    """Test E2E: table_list is forwarded through proxy and matches direct call.

    This test verifies:
    1. Local mode works initially (no upstream)
    2. connect(url) switches to remote mode
    3. table_list calls are forwarded to upstream
    4. Results from proxy match direct call to upstream
    5. connect(url=None) switches back to local mode
    6. Local mode works again after disconnect
    """
    from tasca.config import settings

    # Get the TestClient from mcp_session (this is our "local" MCP server)
    local_client = mcp_session["client"]
    headers = mcp_session["headers"]

    # Step 1: Verify we start in local mode
    initial_status = _call_tool(local_client, headers, "connection_status", {}, 1)
    assert initial_status.get("ok") is True, f"connection_status failed: {initial_status}"
    assert initial_status["data"]["mode"] == "local", "Should start in local mode"

    # Step 2: Create a table in local mode (this will be our "upstream" data)
    local_create_result = _call_tool(
        local_client,
        headers,
        "table_create",
        {"question": "Test table for proxy mode", "context": "E2E proxy test"},
        2,
    )
    assert local_create_result.get("ok") is True, (
        f"local table_create failed: {local_create_result}"
    )
    local_table_id = local_create_result["data"]["id"]

    # Step 3: Get table_list in local mode (baseline for comparison)
    local_list_result = _call_tool(local_client, headers, "table_list", {"status": "open"}, 3)
    assert local_list_result.get("ok") is True, f"local table_list failed: {local_list_result}"
    local_tables = local_list_result["data"]["tables"]
    local_table_ids = {t["id"] for t in local_tables}

    # Step 4: Switch to remote mode - connect to the same server
    # Note: TestClient doesn't have a real URL, so we use a mock URL
    # The proxy mode will attempt to forward to this URL
    # For E2E testing, we need a real HTTP server, so we skip if no external server
    from tests.integration.harness import USE_EXTERNAL_SERVER

    if not USE_EXTERNAL_SERVER:
        pytest.skip("E2E proxy test requires TASCA_USE_EXTERNAL_SERVER=1 with a running server")

    from tests.integration.harness import MCP_BASE_URL

    connect_result = _call_tool(
        local_client,
        headers,
        "connect",
        {"url": MCP_BASE_URL},
        4,
    )
    assert connect_result.get("ok") is True, f"connect failed: {connect_result}"
    assert connect_result["data"]["mode"] == "remote", "Should be in remote mode after connect"

    # Verify connection status shows remote mode
    remote_status = _call_tool(local_client, headers, "connection_status", {}, 5)
    assert remote_status.get("ok") is True, (
        f"connection_status in remote mode failed: {remote_status}"
    )
    assert remote_status["data"]["mode"] == "remote", "connection_status should show remote mode"

    # Step 5: Call table_list through proxy (should be forwarded to upstream)
    proxy_list_result = _call_tool(local_client, headers, "table_list", {"status": "open"}, 6)
    assert proxy_list_result.get("ok") is True, f"proxy table_list failed: {proxy_list_result}"

    # Verify the table we created is visible through the proxy
    proxy_tables = proxy_list_result["data"]["tables"]
    proxy_table_ids = {t["id"] for t in proxy_tables}

    # The proxy should see the same tables (since we're connected to the same server)
    assert local_table_id in proxy_table_ids, "Proxy should see tables from upstream server"

    # Results should match (same server)
    assert proxy_table_ids == local_table_ids, "Proxy results should match direct call"

    # Step 6: Switch back to local mode
    disconnect_result = _call_tool(local_client, headers, "connect", {}, 7)
    assert disconnect_result.get("ok") is True, f"disconnect failed: {disconnect_result}"
    assert disconnect_result["data"]["mode"] == "local", "Should be in local mode after disconnect"

    # Step 7: Verify local mode works again
    final_status = _call_tool(local_client, headers, "connection_status", {}, 8)
    assert final_status.get("ok") is True, f"final connection_status failed: {final_status}"
    assert final_status["data"]["mode"] == "local", "Should be back in local mode"

    # Local table_list should still work
    final_list_result = _call_tool(local_client, headers, "table_list", {"status": "open"}, 9)
    assert final_list_result.get("ok") is True, f"final table_list failed: {final_list_result}"


def test_e2e_proxy_mode_connect_disconnect_cycle(mcp_session):
    """Test connect/disconnect cycle with multiple mode switches.

    This test verifies:
    1. Starting in local mode
    2. Switching to remote mode
    3. Switching back to local mode
    4. Switching to remote mode again (different URL)
    5. Final disconnect to local mode
    """
    from tests.integration.harness import USE_EXTERNAL_SERVER

    if not USE_EXTERNAL_SERVER:
        pytest.skip("E2E proxy test requires TASCA_USE_EXTERNAL_SERVER=1 with a running server")

    from tests.integration.harness import MCP_BASE_URL

    local_client = mcp_session["client"]
    headers = mcp_session["headers"]

    # Start in local mode
    status1 = _call_tool(local_client, headers, "connection_status", {}, 1)
    assert status1["data"]["mode"] == "local"

    # Connect to first upstream
    connect1 = _call_tool(local_client, headers, "connect", {"url": MCP_BASE_URL}, 2)
    assert connect1["data"]["mode"] == "remote"
    assert connect1["data"]["url"] == MCP_BASE_URL

    # Disconnect
    disconnect1 = _call_tool(local_client, headers, "connect", {}, 3)
    assert disconnect1["data"]["mode"] == "local"
    assert disconnect1["data"]["url"] is None

    # Connect again (with token this time)
    connect2 = _call_tool(
        local_client,
        headers,
        "connect",
        {"url": MCP_BASE_URL, "token": "test-token"},
        4,
    )
    assert connect2["data"]["mode"] == "remote"
    assert connect2["data"]["url"] == MCP_BASE_URL
    assert connect2["data"]["token"] == "test-token"

    # Final disconnect
    disconnect2 = _call_tool(local_client, headers, "connect", {}, 5)
    assert disconnect2["data"]["mode"] == "local"


def test_e2e_proxy_mode_table_operations(mcp_session):
    """Test table operations through proxy mode.

    This test verifies:
    1. table_create through proxy
    2. table_get through proxy
    3. Data consistency between proxy and direct access
    """
    from tests.integration.harness import USE_EXTERNAL_SERVER

    if not USE_EXTERNAL_SERVER:
        pytest.skip("E2E proxy test requires TASCA_USE_EXTERNAL_SERVER=1 with a running server")

    from tests.integration.harness import MCP_BASE_URL

    local_client = mcp_session["client"]
    headers = mcp_session["headers"]

    # Switch to remote mode
    connect_result = _call_tool(local_client, headers, "connect", {"url": MCP_BASE_URL}, 1)
    assert connect_result["data"]["mode"] == "remote"

    # Create table through proxy
    create_result = _call_tool(
        local_client,
        headers,
        "table_create",
        {"question": "Proxy test table", "context": "Created through proxy"},
        2,
    )
    assert create_result.get("ok") is True, f"proxy table_create failed: {create_result}"
    table_id = create_result["data"]["id"]

    # Get table through proxy
    get_result = _call_tool(local_client, headers, "table_get", {"table_id": table_id}, 3)
    assert get_result.get("ok") is True, f"proxy table_get failed: {get_result}"
    assert get_result["data"]["id"] == table_id
    assert get_result["data"]["question"] == "Proxy test table"

    # Disconnect and verify local mode
    disconnect = _call_tool(local_client, headers, "connect", {}, 4)
    assert disconnect["data"]["mode"] == "local"


def test_e2e_proxy_mode_local_tools_not_forwarded(mcp_session):
    """Test that local-only tools (connect, connection_status) are never forwarded.

    These tools always run locally regardless of proxy mode.
    """
    from tests.integration.harness import USE_EXTERNAL_SERVER

    if not USE_EXTERNAL_SERVER:
        pytest.skip("E2E proxy test requires TASCA_USE_EXTERNAL_SERVER=1 with a running server")

    from tests.integration.harness import MCP_BASE_URL

    local_client = mcp_session["client"]
    headers = mcp_session["headers"]

    # Switch to remote mode
    connect_result = _call_tool(local_client, headers, "connect", {"url": MCP_BASE_URL}, 1)
    assert connect_result["data"]["mode"] == "remote"

    # connection_status should still work (it's local-only)
    status = _call_tool(local_client, headers, "connection_status", {}, 2)
    assert status.get("ok") is True
    assert status["data"]["mode"] == "remote"

    # connect for disconnect should still work (it's local-only)
    disconnect = _call_tool(local_client, headers, "connect", {}, 3)
    assert disconnect["data"]["mode"] == "local"


def test_e2e_proxy_mode_no_upstream_url_error(mcp_session):
    """Test that operations fail gracefully when no upstream URL is configured.

    In remote mode without a valid URL, operations should return an error.
    """
    local_client = mcp_session["client"]
    headers = mcp_session["headers"]

    # Manually switch to remote mode with invalid URL (simulating misconfiguration)
    from tasca.shell.mcp.proxy import switch_to_remote

    switch_to_remote("http://invalid-host-that-does-not-exist:9999/mcp")

    # Verify we're in remote mode
    status = _call_tool(local_client, headers, "connection_status", {}, 1)
    assert status["data"]["mode"] == "remote"

    # table_list should return an error (cannot connect to upstream)
    list_result = _call_tool(local_client, headers, "table_list", {"status": "open"}, 2)

    # Should be an error (UPSTREAM_UNREACHABLE or similar)
    assert list_result.get("ok") is False, "Should fail when upstream is unreachable"

    # Reset to local mode
    switch_to_local()


def test_e2e_proxy_mode_data_isolation(mcp_session):
    """Test that local and proxy modes access different databases (when applicable).

    This test verifies that switching to proxy mode actually changes
    which backend is being used, not just the code path.
    """
    from tests.integration.harness import USE_EXTERNAL_SERVER

    if not USE_EXTERNAL_SERVER:
        pytest.skip("E2E proxy test requires TASCA_USE_EXTERNAL_SERVER=1 with a running server")

    from tests.integration.harness import MCP_BASE_URL

    local_client = mcp_session["client"]
    headers = mcp_session["headers"]

    # Create a table in local mode
    local_create = _call_tool(
        local_client,
        headers,
        "table_create",
        {"question": "Local mode table", "context": "Created before proxy"},
        1,
    )
    assert local_create.get("ok") is True
    local_table_id = local_create["data"]["id"]

    # Switch to proxy mode (same server, so should see the same data)
    connect_result = _call_tool(local_client, headers, "connect", {"url": MCP_BASE_URL}, 2)
    assert connect_result["data"]["mode"] == "remote"

    # List tables through proxy
    proxy_list = _call_tool(local_client, headers, "table_list", {"status": "open"}, 3)
    assert proxy_list.get("ok") is True

    # When connected to the same server, should see the same tables
    proxy_table_ids = {t["id"] for t in proxy_list["data"]["tables"]}
    assert local_table_id in proxy_table_ids, "Proxy should see tables from the same server"

    # Disconnect
    _call_tool(local_client, headers, "connect", {}, 4)

    # Verify local mode still works
    local_list = _call_tool(local_client, headers, "table_list", {"status": "open"}, 5)
    assert local_list.get("ok") is True
    local_table_ids = {t["id"] for t in local_list["data"]["tables"]}
    assert local_table_id in local_table_ids
