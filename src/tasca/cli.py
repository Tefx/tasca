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
import socket
import sqlite3
import sys
from datetime import UTC, datetime
from typing import Any, cast

from returns.result import Failure, Result, Success

from tasca.config import settings
from tasca.core.domain.table import Table, TableId, TableStatus, Version
from tasca.core.export_service import generate_jsonl, generate_markdown
from tasca.core.schema import create_tables_table_ddl
from tasca.shell.cli_legacy import create_table_via_mcp, create_table_via_rest, is_server_running
from tasca.shell.services.table_id_generator import generate_table_id
from tasca.shell.skills_cli import cmd_skills_install, cmd_skills_list, cmd_skills_show
from tasca.shell.storage.saying_repo import list_all_sayings_by_table
from tasca.shell.storage.table_repo import (
    TableNotFoundError,
    get_table,
)
from tasca.shell.storage.table_repo import (
    create_table as repo_create_table,
)

__all__ = [
    "create_table_via_mcp",
    "create_table_via_rest",
    "is_server_running",
]


def _command_exit_code(command_result: object) -> Result[int, str]:
    """Normalize legacy int commands and Result-based shell commands."""
    if isinstance(command_result, Failure):
        return Failure(str(command_result.failure()))
    if isinstance(command_result, Success):
        return Success(cast(int, command_result.unwrap()))
    return Success(cast(int, command_result))


# @shell_orchestration: Socket I/O for LAN IP discovery (connect to public DNS)
def get_lan_ip() -> Result[str, str]:
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
            return Success(cast(str, s.getsockname()[0]))
    except Exception:
        # Fallback to localhost if detection fails
        return Success("localhost")


def create_table_directly(
    question: str,
    context: str | None,
    db_path: str,
) -> Result[dict[str, Any], str]:
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
            return Failure(f"Failed to generate table ID: {id_result.failure()}")

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
            return Failure(f"Failed to create table: {create_result.failure()}")

        created = create_result.unwrap()

        return Success({
            "id": created.id,
            "question": created.question,
            "context": created.context,
            "status": created.status.value,
            "version": created.version,
            "created_at": created.created_at.isoformat(),
            "updated_at": created.updated_at.isoformat(),
        })
    finally:
        conn.close()


# @shell_complexity: Startup presentation branches only on credential provenance and LAN fallback.
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
    lan_ip_result = get_lan_ip()
    if isinstance(lan_ip_result, Failure):
        lan_ip = "localhost"
    elif isinstance(lan_ip_result, Success):
        lan_ip = lan_ip_result.unwrap()
    else:
        lan_ip = cast(str, lan_ip_result)
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
        print("  Admin token: configured via TASCA_ADMIN_TOKEN (redacted)")
        token_reference = "<TASCA_ADMIN_TOKEN>"
    else:
        print(f"  Admin token: {admin_token}")
        token_reference = admin_token
    print()
    print("  ── Paste to agent ──────────────────────────────────────────")
    print("  Tasca is a shared place for agent discussions and coordination.")
    print(f'  Connect to the table "{question}", read the context, and participate.')
    print("  If more perspectives would help, use Tasca to invite other agents.")
    if token_from_env:
        print("  1. Configure the client with its TASCA_ADMIN_TOKEN value.")
    else:
        print(f'  1. connect(url="http://{lan_ip}:{port}/mcp/", token="{admin_token}")')
    print(f'  2. table_get(table_id="{table_id}")')
    print("  ────────────────────────────────────────────────────────────")
    print()
    # Build HTTP MCP config JSON
    http_config = {
        "mcpServers": {
            "tasca": {
                "url": f"http://{lan_ip}:{port}/mcp",
                "headers": {"Authorization": f"Bearer {token_reference}"},
            }
        }
    }

    print("  First-time agent setup (paste into MCP config):")
    print(f"  STDIO:  {json.dumps(stdio_config, separators=(',', ':'))}")
    print(f"  Remote: {json.dumps(http_config, separators=(',', ':'))}")
    print()
    print("  Ctrl+C to stop. Logs below.")
    print("  ─────────────────────────────────────────────")
    print()  # Trailing newline before server logs


# @shell_orchestration: Start server in foreground, create table directly, print banner
# @shell_complexity: 5 branches for table creation error handling, token selection, and server startup
def cmd_new(args: argparse.Namespace) -> Result[int, str]:
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
        table_result = create_table_directly(question, context, db_path)
        if isinstance(table_result, Failure):
            print(f"Error: {table_result.failure()}", file=sys.stderr)
            return Success(1)
        table_data = table_result.unwrap() if isinstance(table_result, Success) else cast(dict[str, Any], table_result)
    except Exception as e:
        print(f"Error creating table: {e}", file=sys.stderr)
        return Success(1)

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
            access_log=args.verbose,
        )
    except KeyboardInterrupt:
        # Clean shutdown on Ctrl+C
        print("\nTasca server stopped.", file=sys.stderr)
        return Success(0)

    return Success(0)


# @shell_orchestration: Argument parsing and command dispatch is orchestration, not business logic
def cmd_mcp(_args: argparse.Namespace) -> Result[int, str]:
    """Start the MCP stdio server."""
    from tasca.shell.mcp.server import run_mcp_server

    run_mcp_server()
    return Success(0)


def cmd_version(_args: argparse.Namespace) -> Result[int, str]:
    """Print the Tasca version."""
    print(f"tasca {settings.version}")
    return Success(0)


# @shell_complexity: 4 branches for error handling (table not found, DB error, write error)
def cmd_export(args: argparse.Namespace) -> Result[int, str]:
    """Execute the 'export' subcommand.

    Exports a table and its sayings to a file or stdout.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    table_id = args.table_id
    output_format = args.format
    output_file = args.output

    # Get database path from settings (same pattern as cmd_new)
    db_path = settings.db_path

    # Open DB directly
    conn = sqlite3.connect(db_path)

    try:
        # Fetch table
        table_result = get_table(conn, TableId(table_id))
        if isinstance(table_result, Failure):
            error = table_result.failure()
            if isinstance(error, TableNotFoundError):
                print(f"Error: Table not found: {table_id}", file=sys.stderr)
            else:
                print(f"Error: Failed to fetch table: {error}", file=sys.stderr)
            return Success(1)

        table = table_result.unwrap()

        # Fetch ALL sayings for export (no count truncation)
        sayings_result = list_all_sayings_by_table(conn, table_id)
        if isinstance(sayings_result, Failure):
            error_msg = str(sayings_result.failure())
            # Check if it's a size exceeded error
            if "Export size exceeded" in error_msg:
                print(f"Error: {error_msg}", file=sys.stderr)
            else:
                print(f"Error: Failed to fetch sayings: {error_msg}", file=sys.stderr)
            return Success(1)

        sayings = sayings_result.unwrap()

        # Generate export content
        if output_format == "jsonl":
            from datetime import UTC, datetime

            exported_at = datetime.now(UTC).isoformat()
            content = generate_jsonl(table, sayings, exported_at)
        else:
            # Default: markdown
            content = generate_markdown(table, sayings)

        # Write output
        if output_file:
            import pathlib

            path = pathlib.Path(output_file)
            # Ensure parent directory exists
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        else:
            print(content)

        return Success(0)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return Success(1)
    finally:
        conn.close()


# @shell_orchestration: Argument parser configuration for CLI dispatch
def _setup_new_subparser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Setup the 'new' subcommand parser.

    Args:
        subparsers: Subparsers action to add the 'new' command to.
    """
    new_parser = subparsers.add_parser(
        "new",
        help="Create a new discussion table and start server",
        description="Create a new discussion table, print startup banner, and start the HTTP server in foreground.",
    )
    new_parser.add_argument("question", help="The question or topic for discussion")
    new_parser.add_argument(
        "-c", "--context", help="Optional context for the discussion", default=None
    )
    new_parser.add_argument(
        "--host", help="Host to bind when starting server (default: from TASCA_API_HOST)"
    )
    new_parser.add_argument(
        "--port", type=int, help="Port to bind when starting server (default: from TASCA_API_PORT)"
    )
    new_parser.add_argument(
        "-v", "--verbose", action="store_true", default=False, help="Show per-request access logs"
    )
    new_parser.set_defaults(func=cmd_new)


# @shell_orchestration: Argument parser configuration for CLI dispatch
def _setup_export_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Setup the 'export' subcommand parser.

    Args:
        subparsers: Subparsers action to add the 'export' command to.
    """
    export_parser = subparsers.add_parser(
        "export",
        help="Export a table and its sayings",
        description="Export a table and its sayings to a file or stdout.",
    )
    export_parser.add_argument("table_id", help="ID of the table to export")
    export_parser.add_argument(
        "--format",
        "-f",
        choices=["md", "jsonl"],
        default="md",
        help="Export format: md (markdown) or jsonl (default: md)",
    )
    export_parser.add_argument(
        "-o", "--output", help="Output file path (default: stdout)", default=None
    )
    export_parser.set_defaults(func=cmd_export)


# @shell_orchestration: Argument parser configuration for CLI dispatch
def _setup_skills_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> Result[argparse.ArgumentParser, str]:
    """Setup the 'skills' subcommand group parser.

    Args:
        subparsers: Subparsers action to add the 'skills' command to.

    Returns:
        The skills parser for help printing.
    """
    skills_parser = subparsers.add_parser(
        "skills",
        help="Manage bundled agent skills",
        description="List, show, and install bundled agent skills.",
    )
    skills_sub = skills_parser.add_subparsers(dest="skills_command", help="Skills commands")

    skills_sub.add_parser("list", help="List bundled skills").set_defaults(func=cmd_skills_list)

    show_parser = skills_sub.add_parser("show", help="Print skill content to stdout")
    show_parser.add_argument("name", help="Skill name")
    show_parser.set_defaults(func=cmd_skills_show)

    install_parser = skills_sub.add_parser("install", help="Install skill to target directory")
    install_parser.add_argument("name", help="Skill name")
    install_parser.add_argument("--target", required=True, help="Target directory (required)")
    install_parser.set_defaults(func=cmd_skills_install)

    return Success(skills_parser)


# @shell_orchestration: Argument parsing (argparse) and command dispatch to I/O handlers
def main(argv: list[str] | None = None) -> Result[int, str]:
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

    # Setup subcommands
    _setup_new_subparser(subparsers)

    subparsers.add_parser(
        "mcp",
        help="Start the MCP stdio server",
        description="Start the MCP server using stdio transport (for agent integration).",
    ).set_defaults(func=cmd_mcp)

    subparsers.add_parser("version", help="Show Tasca version").set_defaults(func=cmd_version)

    _setup_export_subparser(subparsers)
    skills_parser = _setup_skills_subparser(subparsers).unwrap()

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return Success(1)
    if args.command == "skills" and not hasattr(args, "func"):
        skills_parser.print_help()
        return Success(1)
    exit_code_result = _command_exit_code(args.func(args))
    if isinstance(exit_code_result, Failure):
        print(exit_code_result.failure(), file=sys.stderr)
        return Success(1)
    return Success(exit_code_result.unwrap())


if __name__ == "__main__":
    main_result = main()
    if isinstance(main_result, Failure):
        print(main_result.failure(), file=sys.stderr)
        sys.exit(1)
    sys.exit(main_result.unwrap())
