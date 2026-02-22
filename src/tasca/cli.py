"""
Tasca CLI - Command-line interface for Tasca operations.

This module provides CLI commands for interacting with Tasca services.
Commands can connect via REST API (to a running server) or MCP (stdio).

CLI entry points use SystemExit for error handling and return exit codes,
which is the standard pattern for command-line tools.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any

import httpx

from tasca.config import settings


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
def cmd_new(args: argparse.Namespace) -> int:
    """Execute the 'new' subcommand.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    question = args.question
    context = args.context
    use_mcp = args.mcp
    base_url = args.url or f"http://{settings.api_host}:{settings.api_port}"

    if use_mcp:
        result = create_table_via_mcp(question, context)
    else:
        # Use admin token from args, or from settings
        admin_token = args.token or settings.admin_token
        result = create_table_via_rest(question, context, base_url, admin_token)

    # Output the table ID (the primary return value)
    # MCP response has nested structure: {"ok": true, "data": {"id": ...}}
    # REST API returns the table directly: {"id": ...}
    if "data" in result and isinstance(result.get("data"), dict):
        table_id = result["data"].get("id")
    else:
        table_id = result.get("id")

    if table_id:
        print(table_id)
        return 0
    else:
        print("Error: No table ID in response", file=sys.stderr)
        return 1


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
        help="Create a new discussion table",
        description="Create a new discussion table and return its ID.",
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
        "--mcp",
        action="store_true",
        help="Use MCP stdio transport instead of REST API",
    )
    new_parser.add_argument(
        "-u",
        "--url",
        help="Base URL of the Tasca REST API (default: http://localhost:8000)",
    )
    new_parser.add_argument(
        "-t",
        "--token",
        help="Admin token for authentication (default: from TASCA_ADMIN_TOKEN env)",
    )
    new_parser.set_defaults(func=cmd_new)

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
