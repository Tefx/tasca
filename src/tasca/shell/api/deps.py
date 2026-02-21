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


# Global connection - initialized lazily
_db_connection: sqlite3.Connection | None = None


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Get database connection as a FastAPI dependency.

    Uses a global connection for simplicity. In production,
    this could be replaced with a connection pool.

    Yields:
        SQLite database connection.
    """
    global _db_connection

    if _db_connection is None:
        from pathlib import Path

        from tasca.core.schema import get_all_fts_ddl, get_all_index_ddl, get_all_table_ddl

        db_path = Path(settings.db_path)

        # Create parent directories if needed
        if str(db_path) != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)

        # Connect with check_same_thread=False for async compatibility
        _db_connection = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
        )

        # Enable WAL mode for better concurrency
        _db_connection.execute("PRAGMA journal_mode=WAL")

        # Set busy_timeout for lock handling
        _db_connection.execute("PRAGMA busy_timeout=5000")

        # Enable foreign key constraints
        _db_connection.execute("PRAGMA foreign_keys=ON")

        # Apply schema (tables, indexes, and FTS5 virtual tables/triggers)
        for stmt in get_all_table_ddl() + get_all_index_ddl() + get_all_fts_ddl():
            _db_connection.execute(stmt)
        _db_connection.commit()

    yield _db_connection
