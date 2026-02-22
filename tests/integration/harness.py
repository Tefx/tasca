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

## ASGI Testing (In-Process)

For HTTP transport tests, the harness supports in-process ASGI testing
without requiring an external server:

```python
from tasca.shell.api.app import create_app
from tests.integration.harness import MCPASGIHarness

app = create_app()
async with MCPASGIHarness(app) as harness:
    response = await harness.initialize()
    assert "result" in response
```

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
   - tasca.patron.register - Register a new patron (name, kind, dedup_id)
   - tasca.patron.get - Retrieve patron details by ID

3. **Table Tools**
   - tasca.table.create - Create a discussion table (question, context, dedup_id)
   - tasca.table.join - Join a table by table_id and patron_id
   - tasca.table.get - Get table details by ID
   - tasca.table.say - Add a saying to a table (table_id, content, speaker_kind, patron_id, mentions, dedup_id)
   - tasca.table.listen - Listen for new sayings (table_id, since_sequence, limit)

4. **Seat Tools**
   - tasca.seat.heartbeat - Update seat presence by seat_id
   - tasca.seat.list - List all seats on a table (table_id, active_only)

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
# Run HTTP integration tests with in-process ASGI (no external server needed)
pytest tests/integration/test_mcp.py -v -k "not stdio"

# Run STDIO tests (standalone, uses tasca-mcp command)
pytest tests/integration/test_mcp.py -v -k stdio

# Run with external server (for debugging or CI)
TASCA_USE_EXTERNAL_SERVER=1 uv run tasca &
pytest tests/integration/

# Run with custom base URL
TASCA_TEST_API_URL=http://api.example.com pytest tests/integration/

# Run only REST API tests
pytest tests/integration/test_api.py

# Run only MCP tests
pytest tests/integration/test_mcp.py

# Run E2E proxy tests (requires external server)
# See docs/e2e-testing.md for full documentation
./scripts/run-e2e-external-server.sh -v
# Or manually:
TASCA_USE_EXTERNAL_SERVER=1 pytest tests/integration/test_mcp_proxy.py -v
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
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx
    from fastapi import FastAPI

# =============================================================================
# Base URL Configuration
# =============================================================================

# These values match conftest.py and are configurable via environment variables
# See module docstring for environment variable names

import os

API_BASE_URL = os.environ.get("TASCA_TEST_API_URL", "http://localhost:8000")
MCP_BASE_URL = os.environ.get("TASCA_TEST_MCP_URL", f"{API_BASE_URL}/mcp")
REQUEST_TIMEOUT = int(os.environ.get("TASCA_TEST_TIMEOUT", "30"))

# Environment variable to force external server (skip ASGI fixture)
USE_EXTERNAL_SERVER = os.environ.get("TASCA_USE_EXTERNAL_SERVER", "").lower() in (
    "1",
    "true",
    "yes",
)


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


class MCPHarnessBase(ABC):
    """Abstract base class for MCP test harnesses.

    Provides shared MCP protocol methods and convenience tool wrappers.
    Subclasses implement transport-specific __aenter__ and _send_request.

    Shared by MCPHTTPHarness, MCPASGIHarness, and MCPSTDIOHarness.
    """

    def __init__(self, timeout: float = REQUEST_TIMEOUT) -> None:
        """Initialize MCP harness base.

        Args:
            timeout: Request timeout in seconds
        """
        self.timeout = timeout
        self._request_id = 0

    def _next_id(self) -> int:
        """Get next JSON-RPC request ID."""
        self._request_id += 1
        return self._request_id

    @abstractmethod
    async def _send_request(self, method: str, params: dict | None = None) -> dict:
        """Send a JSON-RPC request.

        Args:
            method: JSON-RPC method name
            params: Optional method parameters

        Returns:
            JSON response as dictionary
        """
        ...

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
        name: str,
        kind: str = "agent",
        dedup_id: str | None = None,
    ) -> dict:
        """Register a new patron.

        Args:
            name: Name or identifier for the patron (used for deduplication).
            kind: Type of patron - 'agent' or 'human' (default 'agent').
            dedup_id: Optional explicit idempotency key for request deduplication.

        Returns:
            Registered patron details with ok, data, and is_new flag.
        """
        args: dict = {"name": name, "kind": kind}
        if dedup_id is not None:
            args["dedup_id"] = dedup_id
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
        question: str,
        context: str | None = None,
        dedup_id: str | None = None,
    ) -> dict:
        """Create a new discussion table.

        Args:
            question: The question or topic for discussion.
            context: Optional context for the discussion.
            dedup_id: Optional explicit idempotency key for request deduplication.

        Returns:
            Created table details.
        """
        args: dict = {"question": question}
        if context is not None:
            args["context"] = context
        if dedup_id is not None:
            args["dedup_id"] = dedup_id
        return await self.call_tool("table_create", args)

    async def table_join(
        self,
        table_id: str,
        patron_id: str,
    ) -> dict:
        """Join a discussion table by creating a seat.

        Args:
            table_id: Human-readable table ID (e.g., "clever-fox-jumps").
            patron_id: UUID of the patron joining the table.

        Returns:
            Table details and seat info.
        """
        return await self.call_tool(
            "table_join",
            {"table_id": table_id, "patron_id": patron_id},
        )

    async def table_get(self, table_id: str) -> dict:
        """Get table details.

        Args:
            table_id: Human-readable table ID (e.g., "clever-fox-jumps").

        Returns:
            Table details
        """
        return await self.call_tool("table_get", {"table_id": table_id})

    async def table_say(
        self,
        table_id: str,
        content: str,
        speaker_kind: str = "agent",
        patron_id: str | None = None,
        speaker_name: str | None = None,
        saying_type: str | None = None,
        mentions: list[str] | None = None,
        reply_to_sequence: int | None = None,
        dedup_id: str | None = None,
    ) -> dict:
        """Append a saying (message) to a table.

        Args:
            table_id: Human-readable table ID (e.g., "clever-fox-jumps").
            content: Markdown content of the saying.
            speaker_kind: Kind of speaker - "agent" or "human". Defaults to "agent".
                If "agent", patron_id is REQUIRED.
                If "human", patron_id MUST be omitted or null.
            patron_id: Patron ID of the speaker (REQUIRED if speaker_kind is "agent").
            speaker_name: Display name of the speaker (optional, derived from patron if not provided).
            saying_type: Type classification of the saying (optional).
            mentions: List of mention handles to resolve (e.g., ["alice", "all"]).
            reply_to_sequence: Sequence number of saying this replies to (optional).
            dedup_id: Optional explicit idempotency key for request deduplication.

        Returns:
            Created saying details.
        """
        args: dict = {
            "table_id": table_id,
            "content": content,
            "speaker_kind": speaker_kind,
        }
        if patron_id is not None:
            args["patron_id"] = patron_id
        if speaker_name is not None:
            args["speaker_name"] = speaker_name
        if saying_type is not None:
            args["saying_type"] = saying_type
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
        since_sequence: int = -1,
        limit: int = 50,
    ) -> dict:
        """Listen for sayings on a table.

        Args:
            table_id: Human-readable table ID (e.g., "clever-fox-jumps").
            since_sequence: Get sayings with sequence > this value (-1 for all).
            limit: Maximum number of sayings to return (default 50).

        Returns:
            List of sayings and next_sequence.
        """
        return await self.call_tool(
            "table_listen",
            {
                "table_id": table_id,
                "since_sequence": since_sequence,
                "limit": limit,
            },
        )

    # =========================================================================
    # Seat Tools
    # =========================================================================

    async def seat_heartbeat(
        self,
        table_id: str,
        patron_id: str | None = None,
        state: str | None = None,
        ttl_ms: int | None = None,
        dedup_id: str | None = None,
        seat_id: str | None = None,
    ) -> dict:
        """Update a seat's heartbeat to indicate presence.

        Args:
            table_id: Human-readable table ID (e.g., "clever-fox-jumps").
            patron_id: UUID of the patron (spec-compliant, preferred).
            state: Seat state - "running", "idle", or "done".
            ttl_ms: Heartbeat timeout in milliseconds.
            dedup_id: Optional explicit idempotency key.
            seat_id: UUID of the seat (deprecated, use patron_id instead).

        Returns:
            expires_at timestamp.
        """
        args: dict = {"table_id": table_id}
        if patron_id is not None:
            args["patron_id"] = patron_id
        if seat_id is not None:
            args["seat_id"] = seat_id
        if state is not None:
            args["state"] = state
        if ttl_ms is not None:
            args["ttl_ms"] = ttl_ms
        if dedup_id is not None:
            args["dedup_id"] = dedup_id
        return await self.call_tool("seat_heartbeat", args)

    async def seat_list(
        self,
        table_id: str,
        active_only: bool = True,
    ) -> dict:
        """List all seats (presences) on a table.

        Args:
            table_id: Human-readable table ID (e.g., "clever-fox-jumps").
            active_only: Filter to active (non-expired) seats only (default True).

        Returns:
            List of seats and active_count.
        """
        return await self.call_tool(
            "seat_list",
            {"table_id": table_id, "active_only": active_only},
        )


class MCPHTTPHarness(MCPHarnessBase):
    """Test harness for MCP HTTP transport.

    Provides convenient methods for testing MCP protocol endpoints with
    automatic resource management.

    Example:
        async with MCPHTTPHarness() as harness:
            result = await harness.initialize()
            assert "result" in result

    Example with auth:
        async with MCPHTTPHarness(admin_token="secret") as harness:
            result = await harness.initialize()
            assert "result" in result
    """

    def __init__(
        self,
        base_url: str = MCP_BASE_URL,
        timeout: float = REQUEST_TIMEOUT,
        admin_token: str | None = None,
    ) -> None:
        """Initialize MCP HTTP harness.

        Args:
            base_url: Base URL for the MCP endpoint
            timeout: Request timeout in seconds
            admin_token: Optional Bearer token for authentication
        """
        super().__init__(timeout=timeout)
        self.base_url = base_url
        self.admin_token = admin_token
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "MCPHTTPHarness":
        """Enter async context and create HTTP client."""
        import httpx

        headers: dict[str, str] = {
            "Accept": "application/json, text/event-stream",
        }
        if self.admin_token:
            headers["Authorization"] = f"Bearer {self.admin_token}"

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout),
            headers=headers,
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


class MCPASGIHarness(MCPHarnessBase):
    """Test harness for MCP using ASGI transport (in-process testing).

    This harness tests MCP endpoints without requiring an external server
    by using httpx ASGI transport. Perfect for unit/integration tests.

    Example:
        from tasca.shell.api.app import create_app

        app = create_app()
        async with MCPASGIHarness(app) as harness:
            result = await harness.initialize()
            assert "result" in result

    Example with auth:
        async with MCPASGIHarness(app, admin_token="secret") as harness:
            result = await harness.initialize()
            assert "result" in result
    """

    def __init__(
        self,
        app: "FastAPI",
        timeout: float = REQUEST_TIMEOUT,
        admin_token: str | None = None,
    ) -> None:
        """Initialize MCP ASGI harness.

        Args:
            app: FastAPI application instance (from create_app())
            timeout: Request timeout in seconds
            admin_token: Optional Bearer token for authentication
        """
        super().__init__(timeout=timeout)
        self.app = app
        self.admin_token = admin_token
        self._client: "httpx.AsyncClient | None" = None

    async def __aenter__(self) -> "MCPASGIHarness":
        """Enter async context and create HTTP client with ASGI transport."""
        import httpx

        # ASGI transport for in-process testing
        # MCP endpoint is at /mcp
        headers: dict[str, str] = {
            "Accept": "application/json, text/event-stream",
        }
        if self.admin_token:
            headers["Authorization"] = f"Bearer {self.admin_token}"

        transport = httpx.ASGITransport(app=self.app)
        self._client = httpx.AsyncClient(
            transport=transport,
            base_url="http://test/mcp",
            timeout=httpx.Timeout(self.timeout),
            headers=headers,
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
            raise RuntimeError("MCPASGIHarness must be used as async context manager")
        return self._client

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


class MCPSTDIOHarness(MCPHarnessBase):
    """Test harness for MCP STDIO transport.

    Provides methods for testing MCP protocol over stdin/stdout
    by spawning the tasca-mcp process.

    Example:
        async with MCPSTDIOHarness() as harness:
            result = await harness.initialize()
            assert "result" in result
    """

    def __init__(
        self,
        timeout: float = REQUEST_TIMEOUT,
        startup_timeout: float = 10.0,
    ) -> None:
        """Initialize MCP STDIO harness.

        Args:
            timeout: Communication timeout in seconds
            startup_timeout: Timeout for process startup in seconds
        """
        super().__init__(timeout=timeout)
        self.startup_timeout = startup_timeout
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._response_queue: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self) -> "MCPSTDIOHarness":
        """Enter async context and start tasca-mcp process."""
        # Wrap subprocess creation in timeout to prevent indefinite hangs
        try:
            self._process = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "uv",
                    "run",
                    "tasca-mcp",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=self.startup_timeout,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"Process startup timed out after {self.startup_timeout}s") from None
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

        request_id = self._next_id()
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
            "table_create - Create a discussion table (question, context, dedup_id)",
            "table_join - Join a table by table_id and patron_id",
            "table_get - Get table details by ID",
            "table_say - Add a saying (table_id, content, speaker_kind, patron_id, mentions, dedup_id)",
            "table_listen - Listen for sayings (table_id, since_sequence, limit)",
        ],
        "mcp_seat": [
            "seat_heartbeat - Update seat presence by patron_id (table_id, patron_id, state, ttl_ms, dedup_id)",
            "seat_list - List all seats on a table (table_id, active_only)",
        ],
        "mcp_stdio": [
            "Process startup and shutdown",
            "Tool invocation via STDIO",
        ],
    }
