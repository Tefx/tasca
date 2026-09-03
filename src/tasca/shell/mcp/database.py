"""
MCP database connection management.

This module manages SQLite connections for MCP tools.
Shell layer - handles I/O (database connections).
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from returns.result import Failure

from tasca.config import settings
from tasca.shell.storage.database import apply_schema

if TYPE_CHECKING:
    from collections.abc import Generator


# Global connection for MCP tools - initialized lazily
_mcp_db_connection: sqlite3.Connection | None = None


def get_mcp_db() -> Generator[sqlite3.Connection]:
    """Get database connection for MCP tools.

    Uses a single global connection for the MCP server lifetime.
    Initializes the schema on first use.

    Yields:
        SQLite database connection.
    """
    global _mcp_db_connection

    if _mcp_db_connection is None:
        from pathlib import Path

        db_path = Path(settings.db_path)

        # Create parent directories if needed
        if str(db_path) != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)

        # Connect with check_same_thread=False for async compatibility
        _mcp_db_connection = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
        )

        # Enable WAL mode for better concurrency
        _mcp_db_connection.execute("PRAGMA journal_mode=WAL")

        # Set busy_timeout for lock handling
        _mcp_db_connection.execute("PRAGMA busy_timeout=5000")

        # Enable foreign key constraints
        _mcp_db_connection.execute("PRAGMA foreign_keys=ON")

        # Use the shared schema path so every transport receives the same migrations.
        schema_result = apply_schema(_mcp_db_connection)
        if isinstance(schema_result, Failure):
            failure = schema_result.failure()
            _mcp_db_connection.close()
            _mcp_db_connection = None
            raise sqlite3.DatabaseError(failure)

    yield _mcp_db_connection


def close_mcp_db() -> None:
    """Close the MCP database connection.

    Should be called during server shutdown.
    """
    global _mcp_db_connection
    if _mcp_db_connection is not None:
        _mcp_db_connection.close()
        _mcp_db_connection = None
