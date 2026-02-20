"""
FastAPI application entry point and MCP server startup.

This module provides the main entry point for running the Tasca service,
which can operate as both an HTTP REST API and an MCP server.
"""

from tasca.config import settings


def main() -> None:
    """Main entry point for the Tasca service."""
    print(f"Tasca v{settings.version} starting...")
    # TODO: Implement FastAPI + MCP server startup


if __name__ == "__main__":
    main()
