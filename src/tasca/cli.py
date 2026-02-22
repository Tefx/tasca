"""
Tasca CLI - Command-line interface for Tasca operations.

This module provides CLI commands for interacting with Tasca services.
Commands can connect via REST API (to a running server) or MCP (stdio).

CLI entry points use SystemExit for error handling and return exit codes,
which is the standard pattern for command-line tools.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from tasca.config import settings
from tasca.core.domain.table import Table, TableStatus, Version
from tasca.core.schema import create_tables_table_ddl
from tasca.shell.services.table_id_generator import generate_table_id
from tasca.shell.storage.table_repo import create_table as repo_create_table
from returns.result import Failure, Success

# Track if we started the server (for cleanup)
_started_server_process: subprocess.Popen[str] | None = None


# @invar:allow shell_result: Helper function returns string for banner, not Result
def get_lan_ip() -> str:
    """Get the LAN IP address for remote access.

    Returns the first non-loopback IPv4 address, or 'localhost' if none found.

    Returns:
        LAN IP address or 'localhost'.
    """
    try:
        # Create a UDP socket to discover the LAN IP
        # This doesn't actually send data, just discovers the interface
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # Connect to a public DNS server (doesn't send data)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        # Fallback to localhost if detection fails
        return "localhost"


# @invar:allow shell_result: Raises RuntimeError on failure, CLI-entry pattern
def create_table_directly(
    question: str,
    context: str | None,
    db_path: str,
) -> dict[str, Any]:
    """Create a table directly in the database without HTTP server.

    Args:
        question: The question or topic for discussion.
        context: Optional context for the discussion.
        db_path: Path to the SQLite database.

    Returns:
        Table data dictionary with id, question, context, status, etc.

    Raises:
        RuntimeError: If table creation fails.
    """
    # Ensure the database directory exists
    import pathlib

    db_file = pathlib.Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)

    try:
        # Ensure tables exist
        conn.execute(create_tables_table_ddl())

        # Generate table ID
        id_result = generate_table_id(conn)
        if isinstance(id_result, Failure):
            raise RuntimeError(f"Failed to generate table ID: {id_result.failure()}")

        table_id = id_result.unwrap()

        now = datetime.now(UTC)

        table = Table(
            id=table_id,
            question=question,
            context=context,
            status=TableStatus.OPEN,
            version=Version(1),
            created_at=now,
            updated_at=now,
        )

        create_result = repo_create_table(conn, table)
        if isinstance(create_result, Failure):
            raise RuntimeError(f"Failed to create table: {create_result.failure()}")

        created = create_result.unwrap()

        return {
            "id": created.id,
            "question": created.question,
            "context": created.context,
            "status": created.status.value,
            "version": created.version,
            "created_at": created.created_at.isoformat(),
            "updated_at": created.updated_at.isoformat(),
        }
    finally:
        conn.close()


def print_startup_banner(
    table_data: dict[str, Any],
    admin_token: str,
    db_path: str,
    port: int,
    token_from_env: bool,
) -> None:
    """Print the startup banner with all connection info.

    Args:
        table_data: Created table data.
        admin_token: The admin token (generated or from env).
        db_path: Database path.
        port: Server port.
        token_from_env: Whether token came from environment variable.
    """
    lan_ip = get_lan_ip()
    table_id = table_data["id"]
    question = table_data["question"]
    status = table_data["status"].upper()

    # Build STDIO MCP config JSON
    stdio_config = {
        "tasca": {
            "command": "uv",
            "args": ["--directory", "/path/to/tasca", "run", "tasca-mcp"],
        }
    }

    # Calculate box width based on content
    version_str = f"TASCA v{settings.version}"
    db_str = f"Database: {db_path}"
    box_inner_width = max(len(version_str), len(db_str), 50)
    box_width = box_inner_width + 4  # 2 for padding + 2 for borders

    # Print banner
    print()  # Leading newline for spacing
    print(f"┌{'─' * box_width}┐")
    print(f"│  {version_str:<{box_inner_width - 2}}  │")
    print(f"│  {db_str:<{box_inner_width - 2}}  │")
    print(f"└{'─' * box_width}┘")
    print()
    print(f'  Table: "{question}"')  # Note: no ANSI colors per spec
    print(f"  ID:    {table_id}")
    print(f"  Status: {status}")
    print()
    print(f"  Web UI:  http://localhost:{port}/tables/{table_id}")
    print(f"  MCP:     http://{lan_ip}:{port}/mcp/")
    print()
    if token_from_env:
        print("  Admin token: (from TASCA_ADMIN_TOKEN env)")
    else:
        print(f"  Admin token: {admin_token}")
    print()
    print("  ── Paste to agent ──────────────────────────────────────────")
    print(f'  Connect to the Tasca discussion table "{question}".')
    print(f"  1. connect(url=\"http://{lan_ip}:{port}/mcp/\", token=\"{admin_token}\")")
    print(f"  2. table_get(table_id=\"{table_id}\")")
    print("  ────────────────────────────────────────────────────────────")
    print()
    print("  First-time agent setup (paste into MCP config):")
    print(f"  {json.dumps(stdio_config, separators=(',', ':'))}")
    print()
    print("  Ctrl+C to stop. Logs below.")
    print("  ─────────────────────────────────────────────")
    print()  # Trailing newline before server logs


# @invar:allow shell_result: CLI entry points use SystemExit for errors, not Result[T, E]
# @shell_complexity: 3 branches for server check logic (connect, port in use error, unexpected error)
def is_server_running(base_url: str) -> bool:
    """Check if the Tasca server is already running.

    Args:
        base_url: Base URL of the Tasca REST API.

    Returns:
        True if server is responding, False otherwise.
    """
    try:
        with httpx.Client(timeout=2.0) as client:
            response = client.get(f"{base_url.rstrip('/')}/api/v1/health")
            return response.status_code == 200
    except httpx.ConnectError:
        return False
    except httpx.TimeoutException:
        return False


# @invar:allow shell_result: CLI entry points use SystemExit for errors, not Result[T, E]
# @shell_complexity: 5 branches for server startup (find module, start, wait, timeout, error)
def start_server_background(host: str, port: int) -> subprocess.Popen[str]:
    """Start the Tasca server in background.

    Args:
        host: Host to bind.
        port: Port to bind.

    Returns:
        The server subprocess.

    Raises:
        SystemExit: If server cannot be started.
    """
    global _started_server_process

    # Use the 'tasca' console script to start the server
    # Pass host and port via environment variables
    env = os.environ.copy()
    env["TASCA_API_HOST"] = host
    env["TASCA_API_PORT"] = str(port)

    try:
        process = subprocess.Popen(
            ["tasca"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _started_server_process = process
        return process
    except FileNotFoundError as e:
        print("Error: Cannot find 'tasca' command to start server", file=sys.stderr)
        print("Make sure tasca is installed: pip install -e .", file=sys.stderr)
        raise SystemExit(1) from e


# @invar:allow shell_result: CLI entry points use SystemExit for errors, not Result[T, E]
# @shell_orchestration: Polling loop that calls is_server_running (which does HTTP I/O)
def wait_for_server_ready(base_url: str, timeout: float = 30.0) -> bool:
    """Wait for the server to become ready.

    Args:
        base_url: Base URL of the Tasca REST API.
        timeout: Maximum seconds to wait.

    Returns:
        True if server became ready, False on timeout.
    """
    start_time = time.monotonic()
    poll_interval = 0.1

    while time.monotonic() - start_time < timeout:
        if is_server_running(base_url):
            return True
        time.sleep(poll_interval)

    return False


def stop_server() -> None:
    """Stop the server if we started it.

    This is called via atexit to ensure cleanup on exit.
    """
    global _started_server_process

    if _started_server_process is not None:
        try:
            # Try graceful shutdown first
            _started_server_process.terminate()
            try:
                _started_server_process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                # Force kill if graceful shutdown didn't work
                _started_server_process.kill()
                _started_server_process.wait(timeout=2.0)
        except Exception:
            pass  # Best-effort cleanup
        finally:
            _started_server_process = None


# Register cleanup handler
atexit.register(stop_server)


# Handle signals for graceful shutdown
def _signal_handler(signum: int, frame: Any) -> None:
    """Handle termination signals by cleaning up the server."""
    stop_server()
    sys.exit(128 + signum)


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


# @invar:allow shell_result: CLI entry points use SystemExit for errors, not Result[T, E]
# @shell_complexity: 6 branches for HTTP error handling (connection, timeout, status codes)
def create_table_via_rest(
    question: str,
    context: str | None,
    base_url: str,
    admin_token: str | None,
) -> dict[str, Any]:
    """Create a table via REST API.

    Args:
        question: The question or topic for discussion.
        context: Optional context for the discussion.
        base_url: Base URL of the Tasca REST API.
        admin_token: Admin token for authentication.

    Returns:
        Response data from the API.

    Raises:
        SystemExit: On connection error or API error.
    """
    url = f"{base_url.rstrip('/')}/api/v1/tables"
    headers = {"Content-Type": "application/json"}
    if admin_token:
        headers["Authorization"] = f"Bearer {admin_token}"

    payload = {"question": question}
    if context:
        payload["context"] = context

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, json=payload, headers=headers)
    except httpx.ConnectError as e:
        print(f"Error: Cannot connect to Tasca server at {base_url}", file=sys.stderr)
        print(f"Make sure the server is running: tasca", file=sys.stderr)
        raise SystemExit(1) from e
    except httpx.TimeoutException as e:
        print(f"Error: Request timed out connecting to {base_url}", file=sys.stderr)
        raise SystemExit(1) from e

    if response.status_code != 200:
        try:
            error_data = response.json()
            detail = error_data.get("detail", response.text)
        except Exception:
            detail = response.text
        print(f"Error: API returned {response.status_code}: {detail}", file=sys.stderr)
        raise SystemExit(1)

    return response.json()


# @invar:allow shell_result: CLI entry points use SystemExit for errors, not Result[T, E]
# @shell_complexity: 12 branches for MCP protocol handling (init, tool call, error paths)
# @invar:allow function_size: MCP protocol requires multi-step handshake and error handling
def create_table_via_mcp(
    question: str,
    context: str | None,
) -> dict[str, Any]:
    """Create a table via MCP stdio.

    Args:
        question: The question or topic for discussion.
        context: Optional context for the discussion.

    Returns:
        Response data from the MCP tool.

    Raises:
        SystemExit: On MCP error or connection error.
    """
    # Build MCP tool call request (JSON-RPC 2.0 format)
    # Initialize request
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

    # Tool call request
    tool_params = {"question": question}
    if context:
        tool_params["context"] = context

    tool_request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "table_create", "arguments": tool_params},
    }

    try:
        # Start the MCP server process using the console script
        process = subprocess.Popen(
            ["tasca-mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Type narrowing: stdin/stdout/stderr are guaranteed non-None with PIPE
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None

        # Send initialize request
        process.stdin.write(json.dumps(init_request) + "\n")
        process.stdin.flush()

        # Read initialize response
        init_response_line = process.stdout.readline()
        if not init_response_line:
            stderr = process.stderr.read()
            print("Error: MCP server did not respond", file=sys.stderr)
            if stderr:
                print(f"MCP stderr: {stderr}", file=sys.stderr)
            raise SystemExit(1)

        init_response = json.loads(init_response_line)
        if "error" in init_response:
            print(f"Error: MCP initialize failed: {init_response['error']}", file=sys.stderr)
            raise SystemExit(1)

        # Send initialized notification
        initialized_notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        process.stdin.write(json.dumps(initialized_notification) + "\n")
        process.stdin.flush()

        # Send tool call request
        process.stdin.write(json.dumps(tool_request) + "\n")
        process.stdin.flush()

        # Read tool response
        response_line = process.stdout.readline()
        if not response_line:
            stderr = process.stderr.read()
            print("Error: MCP server did not respond to tool call", file=sys.stderr)
            if stderr:
                print(f"MCP stderr: {stderr}", file=sys.stderr)
            raise SystemExit(1)

        response = json.loads(response_line)

        # Close stdin to signal end of input
        process.stdin.close()
        process.wait()

        if "error" in response:
            error_info = response["error"]
            message = error_info.get("message", str(error_info))
            print(f"Error: MCP tool error: {message}", file=sys.stderr)
            raise SystemExit(1)

        # Parse the result
        result = response.get("result", {})
        # MCP returns content array with text
        if "content" in result:
            for content in result["content"]:
                if content.get("type") == "text":
                    return json.loads(content["text"])
        return result

    except FileNotFoundError as e:
        print("Error: Cannot find tasca-mcp server", file=sys.stderr)
        raise SystemExit(1) from e
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON response from MCP server: {e}", file=sys.stderr)
        raise SystemExit(1) from e


# @invar:allow shell_result: CLI entry points return exit codes, not Result[T, E]
# @shell_orchestration: Start server in foreground, create table directly, print banner
def cmd_new(args: argparse.Namespace) -> int:
    """Execute the 'new' subcommand.

    Behavior per spec:
    1. Create table directly in database
    2. Print startup banner with connection info
    3. Start HTTP server in foreground (blocking mode)
    4. Ctrl+C to stop

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    question = args.question
    context = args.context

    # Get settings
    host = args.host or settings.api_host
    port = args.port or settings.api_port
    db_path = settings.db_path

    # Step 1: Create table directly in database
    try:
        table_data = create_table_directly(question, context, db_path)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error creating table: {e}", file=sys.stderr)
        return 1

    # Determine admin token: use env var if set, otherwise auto-generate
    if settings.admin_token_from_env:
        admin_token = settings.admin_token
    else:
        # Auto-generate tk_-prefixed token
        import secrets

        admin_token = f"tk_{secrets.token_hex(16)}"

    # Step 2: Print startup banner
    print_startup_banner(
        table_data=table_data,
        admin_token=admin_token,
        db_path=db_path,
        port=port,
        token_from_env=settings.admin_token_from_env,
    )

    # Step 3: Start HTTP server in foreground (blocking mode)
    # Per spec: "Start the FastAPI server (foreground, Ctrl+C to stop)"
    import uvicorn
    from tasca.shell.api.app import create_app

    # Set the admin token in settings for the server to use
    # We need to update settings since we may have generated a new token
    if not settings.admin_token_from_env:
        # Update the settings object with our generated token
        object.__setattr__(settings, "admin_token", admin_token)

    app = create_app()

    # Run server in foreground (blocks until Ctrl+C)
    try:
        uvicorn.run(
            app,
            host=host,
            port=port,
            ws="wsproto",
        )
    except KeyboardInterrupt:
        # Clean shutdown on Ctrl+C
        print("\nTasca server stopped.", file=sys.stderr)
        return 0

    return 0


# @invar:allow shell_result: CLI entry points return exit codes, not Result[T, E]
# @shell_orchestration: Argument parsing and command dispatch is orchestration, not business logic
def main(argv: list[str] | None = None) -> int:
    """Main entry point for the Tasca CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    parser = argparse.ArgumentParser(
        prog="tasca-cli",
        description="Tasca CLI - Command-line interface for Tasca operations",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # 'new' subcommand
    new_parser = subparsers.add_parser(
        "new",
        help="Create a new discussion table and start server",
        description="Create a new discussion table, print startup banner, and start the HTTP server in foreground.",
    )
    new_parser.add_argument(
        "question",
        help="The question or topic for discussion",
    )
    new_parser.add_argument(
        "-c",
        "--context",
        help="Optional context for the discussion",
        default=None,
    )
    new_parser.add_argument(
        "--host",
        help="Host to bind when starting server (default: from TASCA_API_HOST)",
    )
    new_parser.add_argument(
        "--port",
        type=int,
        help="Port to bind when starting server (default: from TASCA_API_PORT)",
    )
    new_parser.set_defaults(func=cmd_new)

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
