"""
Dependency injection for FastAPI routes.

This module provides dependency functions for injecting
repositories and services into route handlers.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from tasca.config import settings

if TYPE_CHECKING:
    from collections.abc import Generator


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Get database connection as a FastAPI dependency.

    Uses connection-per-request pattern with explicit cleanup guarantee.
    Each request gets a fresh connection that is properly closed after use.

    This approach:
    - Avoids connection leakage (each connection is closed)
    - Prevents connection exhaustion under load
    - Ensures transaction isolation between requests
    - Provides proper cleanup on application shutdown

    Yields:
        SQLite database connection.
    """
    from pathlib import Path

    from tasca.core.schema import get_all_fts_ddl, get_all_index_ddl, get_all_table_ddl

    db_path = Path(settings.db_path)

    # Create parent directories if needed
    if str(db_path) != ":memory:":
        db_path.parent.mkdir(parents=True, exist_ok=True)

    # Connect with check_same_thread=False for async compatibility
    conn = sqlite3.connect(
        str(db_path),
        check_same_thread=False,
    )

    try:
        # Enable WAL mode for better concurrency
        conn.execute("PRAGMA journal_mode=WAL")

        # Set busy_timeout for lock handling
        conn.execute("PRAGMA busy_timeout=5000")

        # Enable foreign key constraints
        conn.execute("PRAGMA foreign_keys=ON")

        # Apply schema (tables, indexes, and FTS5 virtual tables/triggers)
        for stmt in get_all_table_ddl() + get_all_index_ddl() + get_all_fts_ddl():
            conn.execute(stmt)
        conn.commit()

        yield conn
    finally:
        # GUARANTEE: Connection is always closed, even on exception
        # This prevents connection leakage
        conn.close()
