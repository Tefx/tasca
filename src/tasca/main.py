"""
FastAPI application entry point and MCP server startup.

This module provides the main entry point for running the Tasca service,
which can operate as both an HTTP REST API and an MCP server.
"""

import uvicorn

from tasca.config import settings
from tasca.shell.api.app import create_app


def main() -> None:
    """Main entry point for the Tasca service."""
    app = create_app()

    # Log startup (no secrets)
    print(f"Tasca v{settings.version} starting...")
    print(f"Host: {settings.api_host}")
    print(f"Port: {settings.api_port}")
    print(f"Database: {settings.db_path}")
    # Note: admin_token is NOT logged (secret)

    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
    )


if __name__ == "__main__":
    main()
