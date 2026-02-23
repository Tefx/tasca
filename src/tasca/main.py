"""
FastAPI application entry point and MCP server startup.

This module provides the main entry point for running the Tasca service,
which can operate as both an HTTP REST API and an MCP server.

It also supports CLI subcommands (e.g., 'tasca new') for direct operations.
"""

import sys

import uvicorn

from tasca.config import settings
from tasca.shell.api.app import create_app
from tasca.cli import get_lan_ip

# CLI subcommands that should be delegated to the CLI handler
CLI_COMMANDS = {"new", "mcp"}


def main() -> None:
    """Main entry point for the Tasca service and CLI.

    If a CLI subcommand is provided (e.g., 'new'), delegates to the CLI handler.
    Otherwise, starts the HTTP REST API server.
    """
    argv = sys.argv[1:]

    # Delegate to CLI if a known command is provided
    if argv and argv[0] in CLI_COMMANDS:
        from tasca.cli import main as cli_main

        sys.exit(cli_main(argv))

    # Handle --help for main command (server start)
    if argv and argv[0] in ("--help", "-h"):
        print("Tasca - A discussion table service for coding agents")
        print()
        print("Usage:")
        print("  tasca                    Start the Tasca HTTP server")
        print("  tasca mcp                Start the MCP stdio server")
        print("  tasca new <question>     Create a new discussion table")
        print()
        print("Commands:")
        print("  mcp        Start the MCP stdio server (for agent integration)")
        print("  new        Create a new discussion table")
        print()
        print("Run 'tasca new --help' for more information on the new command.")
        print()
        print("Server Configuration:")
        print("  TASCA_API_HOST    Host to bind (default: 0.0.0.0)")
        print("  TASCA_API_PORT    Port to bind (default: 8000)")
        print("  TASCA_DB_PATH     Database path (default: ./data/tasca.db)")
        print("  TASCA_ADMIN_TOKEN Admin token for API auth (auto-generated if not set)")
        sys.exit(0)

    # Start the server (default behavior when no command)
    app = create_app()

    lan_ip = get_lan_ip()
    port = settings.api_port
    token = settings.admin_token

    print(f"Tasca v{settings.version} | {settings.db_path}")
    print()
    print(f"  MCP:     http://{lan_ip}:{port}/mcp/")
    print(f"  Web UI:  http://localhost:{port}/")
    print(f"  Token:   {token}")
    print()
    print("  ── Paste to agent ──────────────────────────────────────────")
    print(f'  Tasca MCP server is running.')
    print(f"  connect(url=\"http://{lan_ip}:{port}/mcp/\", token=\"{token}\")")
    print("  ────────────────────────────────────────────────────────────")
    print()

    uvicorn.run(
        app,
        host=settings.api_host,
        port=port,
        ws="wsproto",
    )


if __name__ == "__main__":
    main()
