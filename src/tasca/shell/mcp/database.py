"""
MCP database connection management.

This module manages SQLite connections for MCP tools.
Shell layer - handles I/O (database connections).
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from tasca.config import settings
from tasca.core.schema import get_all_fts_ddl, get_all_index_ddl, get_all_table_ddl

if TYPE_CHECKING:
    from collections.abc import Generator


# Global connection for MCP tools - initialized lazily
_mcp_db_connection: sqlite3.Connection | None = None


def _run_schema_migrations(conn: sqlite3.Connection) -> None:
    """Run schema migrations for backward compatibility.

    This function adds missing columns to existing tables.
    Safe to run multiple times - ALTER TABLE ADD COLUMN is idempotent
    because we check if the column exists first.
    """
    # Migration: Add alias and meta columns to patrons table
    cursor = conn.execute("PRAGMA table_info(patrons)")
    columns = {row[1] for row in cursor.fetchall()}

    if "alias" not in columns:
        conn.execute("ALTER TABLE patrons ADD COLUMN alias TEXT")

    if "meta" not in columns:
        conn.execute("ALTER TABLE patrons ADD COLUMN meta TEXT")


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

        # Apply schema (tables, indexes, and FTS5 virtual tables/triggers)
        for stmt in get_all_table_ddl() + get_all_index_ddl() + get_all_fts_ddl():
            _mcp_db_connection.execute(stmt)
        _mcp_db_connection.commit()

        # Run migrations for backward compatibility
        _run_schema_migrations(_mcp_db_connection)
        _mcp_db_connection.commit()

    yield _mcp_db_connection


def close_mcp_db() -> None:
    """Close the MCP database connection.

    Should be called during server shutdown.
    """
    global _mcp_db_connection
    if _mcp_db_connection is not None:
        _mcp_db_connection.close()
        _mcp_db_connection = None
