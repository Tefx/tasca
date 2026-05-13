"""
Dependency injection for FastAPI routes.

This module provides dependency functions for injecting
repositories and services into route handlers.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from returns.result import Failure

from tasca.config import settings

if TYPE_CHECKING:
    from collections.abc import Generator


def get_db() -> Generator[sqlite3.Connection]:
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

    from tasca.shell.storage.database import apply_schema

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

        # Apply schema and migrations through the storage-owned schema path.
        # CREATE TABLE IF NOT EXISTS alone does not add columns for legacy DBs.
        schema_result = apply_schema(conn)
        if isinstance(schema_result, Failure):
            raise sqlite3.DatabaseError(schema_result.failure())

        yield conn
    finally:
        # GUARANTEE: Connection is always closed, even on exception
        # This prevents connection leakage
        conn.close()
