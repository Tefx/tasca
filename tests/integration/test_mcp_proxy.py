"""
E2E tests for MCP proxy mode (remote forwarding).

These tests verify the proxy mode functionality where:
1. A local MCP client can switch to remote mode via connect(url)
2. Tool calls are forwarded to the upstream server
3. The client can switch back to local mode via connect(url=None)

The upstream server is started as a subprocess via the `upstream_server`
fixture (see conftest.py), ensuring full process isolation and avoiding
module-level singleton conflicts.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tasca.shell.mcp.proxy import switch_to_local

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
    switch_to_local()
    yield
    switch_to_local()


def test_e2e_proxy_mode_table_list_forwarding(mcp_session, upstream_server):
    """Test E2E: table_list is forwarded through proxy to upstream."""
    local_client = mcp_session["client"]
    headers = mcp_session["headers"]
    upstream_url = upstream_server["url"]
    upstream_token = upstream_server["token"]

    # Step 1: Verify we start in local mode
    initial_status = _call_tool(local_client, headers, "connection_status", {}, 1)
    assert initial_status.get("ok") is True, f"connection_status failed: {initial_status}"
    assert initial_status["data"]["mode"] == "local"

    # Step 2: Switch to remote mode (upstream is a separate subprocess)
    connect_result = _call_tool(
        local_client, headers, "connect",
        {"url": upstream_url, "token": upstream_token}, 2,
    )
    assert connect_result.get("ok") is True, f"connect failed: {connect_result}"
    assert connect_result["data"]["mode"] == "remote"

    # Step 3: Call table_list through proxy (forwarded to upstream subprocess)
    proxy_list_result = _call_tool(
        local_client, headers, "table_list", {"status": "open"}, 3,
    )
    assert proxy_list_result.get("ok") is True, f"proxy table_list failed: {proxy_list_result}"
    # Upstream was started with `tasca new`, so it has at least one table
    assert "tables" in proxy_list_result["data"]

    # Step 4: Switch back to local mode
    disconnect_result = _call_tool(local_client, headers, "connect", {}, 4)
    assert disconnect_result.get("ok") is True, f"disconnect failed: {disconnect_result}"
    assert disconnect_result["data"]["mode"] == "local"

    # Step 5: Verify local mode works again
    final_status = _call_tool(local_client, headers, "connection_status", {}, 5)
    assert final_status["data"]["mode"] == "local"


def test_e2e_proxy_mode_connect_disconnect_cycle(mcp_session, upstream_server):
    """Test connect/disconnect cycle with multiple mode switches."""
    local_client = mcp_session["client"]
    headers = mcp_session["headers"]
    upstream_url = upstream_server["url"]
    upstream_token = upstream_server["token"]

    # Start in local mode
    status1 = _call_tool(local_client, headers, "connection_status", {}, 1)
    assert status1["data"]["mode"] == "local"

    # Connect
    connect1 = _call_tool(
        local_client, headers, "connect",
        {"url": upstream_url, "token": upstream_token}, 2,
    )
    assert connect1["data"]["mode"] == "remote"
    assert connect1["data"]["url"] == upstream_url

    # Disconnect
    disconnect1 = _call_tool(local_client, headers, "connect", {}, 3)
    assert disconnect1["data"]["mode"] == "local"
    assert disconnect1["data"]["url"] is None

    # Reconnect
    connect2 = _call_tool(
        local_client, headers, "connect",
        {"url": upstream_url, "token": upstream_token}, 4,
    )
    assert connect2["data"]["mode"] == "remote"
    assert connect2["data"]["has_token"] is True

    # Final disconnect
    disconnect2 = _call_tool(local_client, headers, "connect", {}, 5)
    assert disconnect2["data"]["mode"] == "local"


def test_e2e_proxy_mode_table_operations(mcp_session, upstream_server):
    """Test table operations through proxy mode."""
    local_client = mcp_session["client"]
    headers = mcp_session["headers"]
    upstream_url = upstream_server["url"]
    upstream_token = upstream_server["token"]

    # Switch to remote mode
    connect_result = _call_tool(
        local_client, headers, "connect",
        {"url": upstream_url, "token": upstream_token}, 1,
    )
    assert connect_result["data"]["mode"] == "remote"

    # Create table through proxy (on upstream)
    create_result = _call_tool(
        local_client, headers, "table_create",
        {"question": "Proxy test table", "context": "Created through proxy"}, 2,
    )
    assert create_result.get("ok") is True, f"proxy table_create failed: {create_result}"
    table_id = create_result["data"]["id"]

    # Get table through proxy
    get_result = _call_tool(
        local_client, headers, "table_get", {"table_id": table_id}, 3,
    )
    assert get_result.get("ok") is True, f"proxy table_get failed: {get_result}"
    assert get_result["data"]["id"] == table_id
    assert get_result["data"]["question"] == "Proxy test table"

    # Disconnect
    disconnect = _call_tool(local_client, headers, "connect", {}, 4)
    assert disconnect["data"]["mode"] == "local"


def test_e2e_proxy_mode_local_tools_not_forwarded(mcp_session, upstream_server):
    """Test that local-only tools (connect, connection_status) are never forwarded."""
    local_client = mcp_session["client"]
    headers = mcp_session["headers"]
    upstream_url = upstream_server["url"]
    upstream_token = upstream_server["token"]

    # Switch to remote mode
    connect_result = _call_tool(
        local_client, headers, "connect",
        {"url": upstream_url, "token": upstream_token}, 1,
    )
    assert connect_result["data"]["mode"] == "remote"

    # connection_status should still work locally
    status = _call_tool(local_client, headers, "connection_status", {}, 2)
    assert status.get("ok") is True
    assert status["data"]["mode"] == "remote"

    # Disconnect (also local-only)
    disconnect = _call_tool(local_client, headers, "connect", {}, 3)
    assert disconnect["data"]["mode"] == "local"


def test_e2e_proxy_mode_no_upstream_url_error(mcp_session):
    """Test that operations fail gracefully when upstream URL is unreachable."""
    local_client = mcp_session["client"]
    headers = mcp_session["headers"]

    # Manually switch to remote mode with invalid URL
    from tasca.shell.mcp.proxy import switch_to_remote

    switch_to_remote("http://invalid-host-that-does-not-exist:9999/mcp")

    # Verify we're in remote mode
    status = _call_tool(local_client, headers, "connection_status", {}, 1)
    assert status["data"]["mode"] == "remote"

    # table_list should return an error
    list_result = _call_tool(local_client, headers, "table_list", {"status": "open"}, 2)
    assert list_result.get("ok") is False, "Should fail when upstream is unreachable"

    # Reset
    switch_to_local()
