"""
Integration tests for CLI functionality.

This module tests the tasca new CLI command with actual
REST API and MCP interactions using in-process ASGI testing.

Tests:
- Table creation via REST API (via MCP tools)
- Table creation via MCP protocol

Uses mcp_test_client fixture for in-process ASGI testing.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

import pytest

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


# =============================================================================
# Integration Tests: Table Creation via REST API
# =============================================================================


class TestIntegrationCreateTableViaRest:
    """Integration tests for table creation via REST API.

    These tests use in-process ASGI testing via TestClient,
    so no external server is required.
    """

    def test_create_table_via_rest_asgi(
        self,
        mcp_test_client: "TestClient",
    ) -> None:
        """Create table via REST API using in-process ASGI.

        This test verifies the full flow:
        1. Initialize MCP session
        2. Use the table_create MCP tool to create a table
        3. Verify the response contains a valid table ID

        Note: Uses mcp_test_client because the API is mounted at /api/v1
        but we use MCP tools for table creation in the integration context.
        """
        # Create table via REST-like flow through MCP
        # First initialize MCP session
        init_response = mcp_test_client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "cli-test", "version": "0.1.0"},
                },
            },
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert init_response.status_code == 200

        # Get session ID for subsequent requests
        session_id = init_response.headers.get("mcp-session-id")
        headers = {"Accept": "application/json, text/event-stream"}
        if session_id:
            headers["mcp-session-id"] = session_id

        init_data = _parse_sse_response(init_response.text)
        assert "result" in init_data

        # Create table
        create_response = mcp_test_client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "table_create",
                    "arguments": {
                        "question": "What is the best approach for REST API testing?",
                        "context": "Testing table creation via MCP tool",
                    },
                },
            },
            headers=headers,
        )
        assert create_response.status_code == 200

        create_data = _parse_sse_response(create_response.text)
        assert "result" in create_data

        # Extract table ID from MCP content
        content = create_data["result"].get("content", [])
        assert len(content) > 0
        result_text = content[0].get("text", "{}")
        table_result = json.loads(result_text)

        assert table_result.get("ok") is True
        assert "data" in table_result
        table_id = table_result["data"]["id"]
        assert table_id is not None
        assert table_result["data"]["question"] == "What is the best approach for REST API testing?"
        assert table_result["data"]["status"] == "open"

    def test_create_table_via_rest_with_dedup(
        self,
        mcp_test_client: "TestClient",
    ) -> None:
        """Create table with dedup_id for idempotency.

        Verifies that duplicate requests with same dedup_id
        return the same table ID.
        """
        # Initialize
        init_response = mcp_test_client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "cli-test", "version": "0.1.0"},
                },
            },
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert init_response.status_code == 200

        # Get session ID for subsequent requests
        session_id = init_response.headers.get("mcp-session-id")
        headers = {"Accept": "application/json, text/event-stream"}
        if session_id:
            headers["mcp-session-id"] = session_id

        dedup_id = f"test-dedup-{uuid.uuid4()}"

        # First create
        create_response1 = mcp_test_client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "table_create",
                    "arguments": {
                        "question": "Dedup test question",
                        "dedup_id": dedup_id,
                    },
                },
            },
            headers=headers,
        )
        assert create_response1.status_code == 200
        create_data1 = _parse_sse_response(create_response1.text)
        content1 = create_data1["result"]["content"]
        result1 = json.loads(content1[0]["text"])
        table_id1 = result1["data"]["id"]

        # Second create with same dedup_id
        create_response2 = mcp_test_client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "table_create",
                    "arguments": {
                        "question": "Different question",
                        "dedup_id": dedup_id,
                    },
                },
            },
            headers=headers,
        )
        assert create_response2.status_code == 200
        create_data2 = _parse_sse_response(create_response2.text)
        content2 = create_data2["result"]["content"]
        result2 = json.loads(content2[0]["text"])
        table_id2 = result2["data"]["id"]

        # Should return same table ID (idempotent)
        assert table_id1 == table_id2


# =============================================================================
# Integration Tests: Table Creation via MCP
# =============================================================================


class TestIntegrationCreateTableViaMCP:
    """Integration tests for table creation via MCP.

    Tests the MCP protocol directly using the in-process ASGI handler.
    """

    def test_mcp_table_create_via_asgi(
        self,
        mcp_test_client: "TestClient",
    ) -> None:
        """Test MCP table_create tool via ASGI in-process.

        Verifies the full MCP protocol flow:
        1. Initialize MCP session
        2. Call table_create tool
        3. Parse JSON-RPC response

        This simulates what create_table_via_mcp() does internally.
        """
        # Initialize MCP session
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "tasca-cli", "version": "1.0.0"},
            },
        }

        init_response = mcp_test_client.post(
            "/mcp",
            json=init_request,
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert init_response.status_code == 200

        # Get session ID for subsequent requests
        session_id = init_response.headers.get("mcp-session-id")
        headers = {"Accept": "application/json, text/event-stream"}
        if session_id:
            headers["mcp-session-id"] = session_id

        # Parse SSE format response
        init_result = _parse_sse_response(init_response.text)

        assert "result" in init_result
        assert "protocolVersion" in init_result["result"]

        # Call table_create tool
        tool_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "table_create",
                "arguments": {
                    "question": "What is the best approach for MCP testing?",
                    "context": "Testing via ASGI transport",
                },
            },
        }

        tool_response = mcp_test_client.post(
            "/mcp",
            json=tool_request,
            headers=headers,
        )
        assert tool_response.status_code == 200

        # Parse tool response
        tool_result = _parse_sse_response(tool_response.text)

        assert "result" in tool_result

        # Extract content
        content = tool_result["result"].get("content", [])
        assert len(content) > 0
        text_content = content[0].get("text", "{}")
        table_data = json.loads(text_content)

        assert table_data.get("ok") is True
        assert "data" in table_data
        assert table_data["data"]["status"] == "open"

    def test_mcp_table_create_with_context(
        self,
        mcp_test_client: "TestClient",
    ) -> None:
        """Test MCP table_create with optional context."""
        # Initialize session
        init_response = mcp_test_client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "tasca-cli", "version": "1.0.0"},
                },
            },
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert init_response.status_code == 200

        # Get session ID for subsequent requests
        session_id = init_response.headers.get("mcp-session-id")
        headers = {"Accept": "application/json, text/event-stream"}
        if session_id:
            headers["mcp-session-id"] = session_id

        # Create table with context
        tool_response = mcp_test_client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "table_create",
                    "arguments": {
                        "question": "How should we structure our API?",
                        "context": "Consider REST best practices and versioning",
                    },
                },
            },
            headers=headers,
        )
        assert tool_response.status_code == 200

        # Parse response
        result = _parse_sse_response(tool_response.text)
        content = result["result"]["content"]
        table_data = json.loads(content[0]["text"])

        assert table_data["ok"] is True
        # Context should be stored in the table
        assert table_data["data"]["context"] == "Consider REST best practices and versioning"

    def test_mcp_table_create_minimal(
        self,
        mcp_test_client: "TestClient",
    ) -> None:
        """Test MCP table_create with only required question field."""
        # Initialize session
        init_response = mcp_test_client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "tasca-cli", "version": "1.0.0"},
                },
            },
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert init_response.status_code == 200

        # Get session ID for subsequent requests
        session_id = init_response.headers.get("mcp-session-id")
        headers = {"Accept": "application/json, text/event-stream"}
        if session_id:
            headers["mcp-session-id"] = session_id

        # Create table with minimal args (question only)
        tool_response = mcp_test_client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "table_create",
                    "arguments": {
                        "question": "Simple question?",
                    },
                },
            },
            headers=headers,
        )
        assert tool_response.status_code == 200

        result = _parse_sse_response(tool_response.text)
        content = result["result"]["content"]
        table_data = json.loads(content[0]["text"])

        assert table_data["ok"] is True
        assert table_data["data"]["question"] == "Simple question?"
        # Context should be None when not provided
        assert table_data["data"].get("context") is None
