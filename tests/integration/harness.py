"""
Integration Test Harness for Tasca.

This module provides the main integration test harness for testing both
REST API and MCP endpoints. It includes utilities for server lifecycle
management, test scenarios, and result verification.

## Architecture

The harness supports two transport modes:
1. **REST API**: HTTP-based endpoints at `/tables`, `/health`, etc.
2. **MCP HTTP**: MCP protocol over HTTP at `/mcp` endpoint
3. **MCP STDIO**: MCP protocol over stdin/stdout via `tasca-mcp` command

## Base URL Configuration

All base URLs are configurable via environment variables:

    TASCA_TEST_API_URL=http://localhost:8000      # REST API base URL
    TASCA_TEST_MCP_URL=http://localhost:8000/mcp  # MCP HTTP base URL
    TASCA_TEST_TIMEOUT=30                         # Request timeout (seconds)

## Test Scenarios

The harness covers the following scenarios:

### REST API Scenarios

1. **Health Checks**
   - GET /api/v1/health - Returns server health status
   - GET /api/v1/ready - Returns readiness status

2. **Table Operations**
   - POST /api/v1/tables - Create a new discussion table
   - GET /api/v1/tables/{table_id} - Retrieve a table by ID
   - GET /api/v1/tables - List all tables
   - DELETE /api/v1/tables/{table_id} - Delete a table

3. **Error Handling**
   - 404 Not Found for non-existent resources
   - 422 Unprocessable Entity for invalid input
   - 500 Internal Server Error handling

### MCP HTTP Scenarios

1. **Protocol Operations**
   - initialize - Establish MCP session
   - tools/list - List available tools
   - tools/call - Invoke a tool

2. **Patron Tools**
   - tasca.patron.register - Register a new patron
   - tasca.patron.get - Retrieve patron details

3. **Table Tools**
   - tasca.table.create - Create a discussion table
   - tasca.table.join - Join a table via invite code
   - tasca.table.get - Get table details
   - tasca.table.say - Add a saying to a table
   - tasca.table.listen - Listen for new sayings

4. **Seat Tools**
   - tasca.seat.heartbeat - Update seat presence
   - tasca.seat.list - List all seats on a table

### MCP STDIO Scenarios

1. **Process Lifecycle**
   - Start tasca-mcp process
   - Send JSON-RPC messages via stdin
   - Read responses from stdout
   - Graceful shutdown

2. **Tool Invocation**
   - Same tools as HTTP transport
   - Validates STDIO transport works correctly

## Usage

### Running Tests

```bash
# Start the server (in one terminal)
uv run tasca

# Run integration tests (in another terminal)
pytest tests/integration/

# Run with custom base URL
TASCA_TEST_API_URL=http://api.example.com pytest tests/integration/

# Run only REST API tests
pytest tests/integration/test_api.py

# Run only MCP tests
pytest tests/integration/test_mcp.py

# Run STDIO tests (doesn't require server)
pytest tests/integration/test_mcp.py -k stdio
```

### Using the Harness Programmatically

```python
from tests.integration.harness import RESTHarness, MCPHTTPHarness

# REST API harness
async with RESTHarness() as harness:
    health = await harness.health_check()
    assert health["status"] == "healthy"

# MCP HTTP harness
async with MCPHTTPHarness() as harness:
    await harness.initialize()
    tools = await harness.list_tools()
    assert "tools" in tools
```
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx

# =============================================================================
# Base URL Configuration
# =============================================================================

# These values match conftest.py and are configurable via environment variables
# See module docstring for environment variable names

import os

API_BASE_URL = os.environ.get("TASCA_TEST_API_URL", "http://localhost:8000")
MCP_BASE_URL = os.environ.get("TASCA_TEST_MCP_URL", f"{API_BASE_URL}/mcp/mcp")
REQUEST_TIMEOUT = int(os.environ.get("TASCA_TEST_TIMEOUT", "30"))


# =============================================================================
# Harness Classes
# =============================================================================


class RESTHarness:
    """Test harness for REST API endpoints.

    Provides convenient methods for testing REST API endpoints with
    automatic resource management.

    Example:
        async with RESTHarness() as harness:
            response = await harness.create_table({"question": "Test?"})
            assert response.status_code == 200
    """

    def __init__(
        self,
        base_url: str = API_BASE_URL,
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        """Initialize REST harness.

        Args:
            base_url: Base URL for the REST API
            timeout: Request timeout in seconds
        """
        self.base_url = base_url
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "RESTHarness":
        """Enter async context and create HTTP client."""
        import httpx

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout),
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit async context and close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> "httpx.AsyncClient":
        """Get the HTTP client.

        Raises:
            RuntimeError: If harness is not used as async context manager

        Returns:
            Configured httpx AsyncClient
        """
        if self._client is None:
            raise RuntimeError("RESTHarness must be used as async context manager")
        return self._client

    # =========================================================================
    # Health Endpoints
    # =========================================================================

    # API v1 prefix for all REST endpoints
    API_V1_PREFIX = "/api/v1"

    async def health_check(self) -> "httpx.Response":
        """Check server health.

        Returns:
            Response from GET /api/v1/health
        """
        return await self.client.get(f"{self.API_V1_PREFIX}/health")

    async def readiness_check(self) -> "httpx.Response":
        """Check server readiness.

        Returns:
            Response from GET /api/v1/ready
        """
        return await self.client.get(f"{self.API_V1_PREFIX}/ready")

    # =========================================================================
    # Table Endpoints
    # =========================================================================

    async def create_table(self, data: dict, admin_token: str | None = None) -> "httpx.Response":
        """Create a new table.

        Args:
            data: Table creation data
            admin_token: Optional admin Bearer token for authenticated requests

        Returns:
            Response from POST /api/v1/tables
        """
        headers = {}
        if admin_token:
            headers["Authorization"] = f"Bearer {admin_token}"
        return await self.client.post(f"{self.API_V1_PREFIX}/tables", json=data, headers=headers)

    async def get_table(self, table_id: str) -> "httpx.Response":
        """Get a table by ID.

        Args:
            table_id: The table identifier

        Returns:
            Response from GET /api/v1/tables/{table_id}
        """
        return await self.client.get(f"{self.API_V1_PREFIX}/tables/{table_id}")

    async def list_tables(self) -> "httpx.Response":
        """List all tables.

        Returns:
            Response from GET /api/v1/tables
        """
        return await self.client.get(f"{self.API_V1_PREFIX}/tables")

    async def delete_table(self, table_id: str, admin_token: str | None = None) -> "httpx.Response":
        """Delete a table by ID.

        Args:
            table_id: The table identifier
            admin_token: Optional admin Bearer token for authenticated requests

        Returns:
            Response from DELETE /api/v1/tables/{table_id}
        """
        headers = {}
        if admin_token:
            headers["Authorization"] = f"Bearer {admin_token}"
        return await self.client.delete(f"{self.API_V1_PREFIX}/tables/{table_id}", headers=headers)


class MCPHTTPHarness:
    """Test harness for MCP HTTP transport.

    Provides convenient methods for testing MCP protocol endpoints with
    automatic resource management.

    Example:
        async with MCPHTTPHarness() as harness:
            result = await harness.initialize()
            assert "result" in result
    """

    def __init__(
        self,
        base_url: str = MCP_BASE_URL,
        timeout: float = REQUEST_TIMEOUT,
    ) -> None:
        """Initialize MCP HTTP harness.

        Args:
            base_url: Base URL for the MCP endpoint
            timeout: Request timeout in seconds
        """
        self.base_url = base_url
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._request_id = 0

    async def __aenter__(self) -> "MCPHTTPHarness":
        """Enter async context and create HTTP client."""
        import httpx

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout),
            headers={
                "Accept": "application/json, text/event-stream",
            },
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit async context and close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> "httpx.AsyncClient":
        """Get the HTTP client.

        Raises:
            RuntimeError: If harness is not used as async context manager

        Returns:
            Configured httpx AsyncClient
        """
        if self._client is None:
            raise RuntimeError("MCPHTTPHarness must be used as async context manager")
        return self._client

    def _next_id(self) -> int:
        """Get next JSON-RPC request ID."""
        self._request_id += 1
        return self._request_id

    async def _send_request(self, method: str, params: dict | None = None) -> dict:
        """Send a JSON-RPC request.

        Args:
            method: JSON-RPC method name
            params: Optional method parameters

        Returns:
            JSON response as dictionary
        """
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params or {},
        }
        response = await self.client.post("/", json=payload)
        response.raise_for_status()

        # FastMCP returns SSE format: "event: message\ndata: {...}\n\n"
        text = response.text
        if text.startswith("event:"):
            # Parse SSE format
            for line in text.split("\n"):
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())

        # Fallback to regular JSON
        return response.json()

    # =========================================================================
    # MCP Protocol Operations
    # =========================================================================

    async def initialize(
        self,
        client_name: str = "tasca-test-client",
        client_version: str = "0.1.0",
        protocol_version: str = "2024-11-05",
    ) -> dict:
        """Initialize MCP session.

        Args:
            client_name: Client name to send
            client_version: Client version to send
            protocol_version: MCP protocol version

        Returns:
            Server capabilities and info
        """
        return await self._send_request(
            "initialize",
            {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {
                    "name": client_name,
                    "version": client_version,
                },
            },
        )

    async def list_tools(self) -> dict:
        """List available MCP tools.

        Returns:
            List of available tools
        """
        return await self._send_request("tools/list")

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Call an MCP tool.

        Args:
            name: Tool name (e.g., "patron_register")
            arguments: Tool arguments

        Returns:
            Tool execution result
        """
        return await self._send_request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )

    # =========================================================================
    # Patron Tools
    # =========================================================================

    async def patron_register(
        self,
        patron_id: str | None = None,
        display_name: str | None = None,
        alias: str | None = None,
        meta: dict | None = None,
    ) -> dict:
        """Register a new patron.

        Args:
            patron_id: Optional patron ID (auto-generated if omitted)
            display_name: Human-readable name
            alias: Short alias for mentions
            meta: Optional metadata

        Returns:
            Registered patron details
        """
        args: dict = {}
        if patron_id is not None:
            args["patron_id"] = patron_id
        if display_name is not None:
            args["display_name"] = display_name
        if alias is not None:
            args["alias"] = alias
        if meta is not None:
            args["meta"] = meta
        return await self.call_tool("patron_register", args)

    async def patron_get(self, patron_id: str) -> dict:
        """Get patron details.

        Args:
            patron_id: Patron UUID

        Returns:
            Patron details
        """
        return await self.call_tool("patron_get", {"patron_id": patron_id})

    # =========================================================================
    # Table Tools
    # =========================================================================

    async def table_create(
        self,
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
            created_by: Patron ID of creator
            title: Table title
            host_ids: Optional list of host patron IDs
            metadata: Optional metadata
            policy: Optional policy config
            board: Optional board data
            dedup_id: Optional deduplication ID

        Returns:
            Created table details
        """
        args: dict = {"created_by": created_by, "title": title}
        if host_ids is not None:
            args["host_ids"] = host_ids
        if metadata is not None:
            args["metadata"] = metadata
        if policy is not None:
            args["policy"] = policy
        if board is not None:
            args["board"] = board
        if dedup_id is not None:
            args["dedup_id"] = dedup_id
        return await self.call_tool("table_create", args)

    async def table_join(
        self,
        invite_code: str,
        patron_id: str | None = None,
        history_limit: int = 10,
        history_max_bytes: int = 65536,
    ) -> dict:
        """Join a table by invite code.

        Args:
            invite_code: Invite code or tasca:// URL
            patron_id: Optional patron ID joining
            history_limit: Max sayings to fetch
            history_max_bytes: Max bytes of history

        Returns:
            Table details and initial sayings
        """
        args: dict = {
            "invite_code": invite_code,
            "history_limit": history_limit,
            "history_max_bytes": history_max_bytes,
        }
        if patron_id is not None:
            args["patron_id"] = patron_id
        return await self.call_tool("table_join", args)

    async def table_get(self, table_id: str) -> dict:
        """Get table details.

        Args:
            table_id: Table UUID

        Returns:
            Table details
        """
        return await self.call_tool("table_get", {"table_id": table_id})

    async def table_say(
        self,
        table_id: str,
        content: str,
        patron_id: str | None = None,
        speaker_kind: str = "agent",
        saying_type: str = "text",
        mentions: list[str] | None = None,
        reply_to_sequence: int | None = None,
        dedup_id: str | None = None,
    ) -> dict:
        """Add a saying to a table.

        Args:
            table_id: Table UUID
            content: Saying content
            patron_id: Speaker patron ID
            speaker_kind: "agent" or "human"
            saying_type: Type of saying
            mentions: List of patron IDs to mention
            reply_to_sequence: Sequence number being replied to
            dedup_id: Deduplication ID

        Returns:
            Created saying details
        """
        args: dict = {
            "table_id": table_id,
            "content": content,
            "speaker_kind": speaker_kind,
            "saying_type": saying_type,
        }
        if patron_id is not None:
            args["patron_id"] = patron_id
        if mentions is not None:
            args["mentions"] = mentions
        if reply_to_sequence is not None:
            args["reply_to_sequence"] = reply_to_sequence
        if dedup_id is not None:
            args["dedup_id"] = dedup_id
        return await self.call_tool("table_say", args)

    async def table_listen(
        self,
        table_id: str,
        since_sequence: int = 0,
        limit: int = 50,
        include_table: bool = True,
    ) -> dict:
        """Listen for new sayings.

        Args:
            table_id: Table UUID
            since_sequence: Get sayings after this sequence
            limit: Max sayings to return
            include_table: Include table snapshot

        Returns:
            List of sayings and next_sequence
        """
        return await self.call_tool(
            "table_listen",
            {
                "table_id": table_id,
                "since_sequence": since_sequence,
                "limit": limit,
                "include_table": include_table,
            },
        )

    # =========================================================================
    # Seat Tools
    # =========================================================================

    async def seat_heartbeat(
        self,
        table_id: str,
        patron_id: str,
        state: str = "running",
        ttl_ms: int = 60000,
    ) -> dict:
        """Update seat presence.

        Args:
            table_id: Table UUID
            patron_id: Patron ID
            state: "running", "idle", or "done"
            ttl_ms: Time-to-live in milliseconds

        Returns:
            Seat details with expires_at
        """
        return await self.call_tool(
            "seat_heartbeat",
            {"table_id": table_id, "patron_id": patron_id, "state": state, "ttl_ms": ttl_ms},
        )

    async def seat_list(self, table_id: str) -> dict:
        """List seats on a table.

        Args:
            table_id: Table UUID

        Returns:
            List of active seats
        """
        return await self.call_tool("seat_list", {"table_id": table_id})


class MCPSTDIOHarness:
    """Test harness for MCP STDIO transport.

    Provides methods for testing MCP protocol over stdin/stdout
    by spawning the tasca-mcp process.

    Example:
        async with MCPSTDIOHarness() as harness:
            result = await harness.initialize()
            assert "result" in result
    """

    def __init__(self, timeout: float = REQUEST_TIMEOUT) -> None:
        """Initialize MCP STDIO harness.

        Args:
            timeout: Communication timeout in seconds
        """
        self.timeout = timeout
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._reader_task: asyncio.Task | None = None
        self._response_queue: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self) -> "MCPSTDIOHarness":
        """Enter async context and start tasca-mcp process."""
        self._process = await asyncio.create_subprocess_exec(
            "uv",
            "run",
            "tasca-mcp",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Start background reader task
        self._reader_task = asyncio.create_task(self._read_responses())
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Exit async context and terminate process."""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            self._process = None

    async def _read_responses(self) -> None:
        """Background task to read responses from stdout."""
        if not self._process or not self._process.stdout:
            return

        buffer = ""
        while True:
            try:
                chunk = await self._process.stdout.read(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8")

                # Process complete JSON lines
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        try:
                            response = json.loads(line)
                            await self._response_queue.put(response)
                        except json.JSONDecodeError:
                            pass  # Ignore non-JSON lines
            except asyncio.CancelledError:
                break

    def _next_id(self) -> int:
        """Get next JSON-RPC request ID."""
        self._request_id += 1
        return self._request_id

    async def _send_request(self, method: str, params: dict | None = None) -> dict:
        """Send a JSON-RPC request via stdin.

        Args:
            method: JSON-RPC method name
            params: Optional method parameters

        Returns:
            JSON response as dictionary
        """
        if not self._process or not self._process.stdin:
            raise RuntimeError("MCPSTDIOHarness must be used as async context manager")

        request_id = self._request_id
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }

        # Send request
        message = json.dumps(payload) + "\n"
        self._process.stdin.write(message.encode("utf-8"))
        await self._process.stdin.drain()

        # Wait for response with matching ID
        try:
            while True:
                response = await asyncio.wait_for(
                    self._response_queue.get(),
                    timeout=self.timeout,
                )
                if response.get("id") == request_id:
                    return response
                # Put back if not for us
                await self._response_queue.put(response)
                await asyncio.sleep(0.01)
        except asyncio.TimeoutError:
            raise TimeoutError(f"No response received for request {request_id}")

    async def initialize(
        self,
        client_name: str = "tasca-test-client",
        client_version: str = "0.1.0",
        protocol_version: str = "2024-11-05",
    ) -> dict:
        """Initialize MCP session.

        Args:
            client_name: Client name to send
            client_version: Client version to send
            protocol_version: MCP protocol version

        Returns:
            Server capabilities and info
        """
        return await self._send_request(
            "initialize",
            {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {
                    "name": client_name,
                    "version": client_version,
                },
            },
        )

    async def list_tools(self) -> dict:
        """List available MCP tools.

        Returns:
            List of available tools
        """
        return await self._send_request("tools/list")

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Call an MCP tool.

        Args:
            name: Tool name
            arguments: Tool arguments

        Returns:
            Tool execution result
        """
        return await self._send_request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )


# =============================================================================
# Utility Functions
# =============================================================================


async def is_server_running(url: str = API_BASE_URL, timeout: float = 5.0) -> bool:
    """Check if the server is running.

    Args:
        url: Server base URL
        timeout: Connection timeout in seconds

    Returns:
        True if server is running, False otherwise
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            response = await client.get(f"{url}/api/v1/health")
            return response.status_code == 200
    except Exception:
        return False


def get_scenarios() -> dict[str, list[str]]:
    """Get list of all test scenarios covered by the harness.

    Returns:
        Dictionary mapping category to list of scenario names
    """
    return {
        "rest_health": [
            "GET /api/v1/health - Returns healthy status",
            "GET /api/v1/ready - Returns ready status",
        ],
        "rest_tables": [
            "POST /api/v1/tables - Create a new table",
            "GET /api/v1/tables/{id} - Retrieve table by ID",
            "GET /api/v1/tables - List all tables",
            "DELETE /api/v1/tables/{id} - Delete a table",
        ],
        "rest_errors": [
            "404 Not Found for non-existent resources",
            "422 Unprocessable Entity for invalid input",
            "500 Internal Server Error handling",
        ],
        "mcp_protocol": [
            "initialize - Establish MCP session",
            "tools/list - List available tools",
        ],
        "mcp_patron": [
            "patron_register - Register a new patron",
            "patron_get - Retrieve patron details",
        ],
        "mcp_table": [
            "table_create - Create a discussion table",
            "table_join - Join a table via invite code",
            "table_get - Get table details",
            "table_say - Add a saying to a table",
            "table_listen - Listen for new sayings",
        ],
        "mcp_seat": [
            "seat_heartbeat - Update seat presence",
            "seat_list - List all seats on a table",
        ],
        "mcp_stdio": [
            "Process startup and shutdown",
            "Tool invocation via STDIO",
        ],
    }
