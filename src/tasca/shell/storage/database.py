"""
Database connection and WAL configuration.

This module manages SQLite connections with proper WAL mode setup.
Shell layer - handles I/O (file paths, database connections).
"""

import sqlite3
from pathlib import Path

from returns.result import Failure, Result, Success

from tasca.core.database_normalization import (
    build_database_config,
    column_names_from_pragma_rows,
    is_memory_database_path,
    normalize_busy_timeout,
    normalize_foreign_keys_enabled,
    normalize_journal_mode,
)
from tasca.core.schema import (
    get_all_fts_ddl,
    get_all_index_ddl,
    get_all_table_ddl,
)

# Default busy timeout in milliseconds
DEFAULT_BUSY_TIMEOUT = 5000


# @shell_complexity: 4 branches for directory creation + WAL mode check + :memory: handling
def init_database(db_path: Path) -> Result[sqlite3.Connection, str]:
    """
    Initialize database connection with WAL mode and busy_timeout.

    Creates the database file and parent directories if needed.
    Configures:
    - WAL mode for better concurrency
    - busy_timeout for lock handling
    - Foreign key enforcement

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Success with Connection, or Failure with error message.

    Example:
        >>> result = init_database(Path(":memory:"))
        >>> isinstance(result, Success)
        True
        >>> conn = result.unwrap()
        >>> conn.execute("PRAGMA journal_mode").fetchone()[0]
        'memory'
        >>> conn.close()
    """
    try:
        # Create parent directories if needed (skip for :memory:)
        db_path_value = str(db_path)
        if not is_memory_database_path(db_path_value):
            db_path.parent.mkdir(parents=True, exist_ok=True)

        # Connect to database
        conn = sqlite3.connect(db_path_value)

        # Enable WAL mode for better concurrency
        conn.execute("PRAGMA journal_mode=WAL").fetchone()

        # Set busy_timeout (5 seconds) for lock handling
        conn.execute(f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT}")

        # Enable foreign key constraints
        conn.execute("PRAGMA foreign_keys=ON")

        return Success(conn)

    except sqlite3.Error as e:
        return Failure(f"Database initialization failed: {e}")
    except OSError as e:
        return Failure(f"Failed to create database directory: {e}")


# @shell_orchestration: Schema version management - orchestration over connection
def get_schema_version(conn: sqlite3.Connection) -> Result[int, str]:
    """
    Get the current schema version from the database.

    Returns Success(0) if no schema version table exists.

    >>> conn = sqlite3.connect(":memory:")
    >>> get_schema_version(conn).unwrap()
    0
    >>> conn.close()
    """
    try:
        cursor = conn.execute("SELECT value FROM schema_version WHERE key = 'version'")
        row = cursor.fetchone()
        return Success(int(row[0]) if row else 0)
    except sqlite3.OperationalError:
        return Success(0)
    except sqlite3.Error as e:
        return Failure(f"Failed to get schema version: {e}")


# @shell_orchestration: Schema version management - orchestration over connection
def set_schema_version(conn: sqlite3.Connection, version: int) -> Result[None, str]:
    """
    Set the schema version in the database.

    Creates the schema_version table if it doesn't exist.

    >>> conn = sqlite3.connect(":memory:")
    >>> set_schema_version(conn, 1).unwrap() is None
    True
    >>> get_schema_version(conn).unwrap()
    1
    >>> conn.close()
    """
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (key, value) VALUES (?, ?)",
            ("version", str(version)),
        )
        conn.commit()
        return Success(None)
    except sqlite3.Error as e:
        conn.rollback()
        return Failure(f"Failed to set schema version: {e}")


def apply_schema(conn: sqlite3.Connection) -> Result[int, str]:
    """
    Apply all schema tables, indexes, and FTS5 to the database.

    Creates tables if they don't exist. Idempotent.

    Args:
        conn: Database connection.

    Returns:
        Success with number of statements applied, or Failure with error.

    >>> conn = sqlite3.connect(":memory:")
    >>> result = apply_schema(conn)
    >>> isinstance(result, Success)
    True
    >>> result.unwrap()  # 6 tables + 8 indexes + 4 FTS = 18 statements
    18
    >>> tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    >>> 'patrons' in [t[0] for t in tables]
    True
    >>> 'sayings' in [t[0] for t in tables]
    True
    >>> 'sayings_fts' in [t[0] for t in tables]  # FTS5 virtual table
    True
    >>> conn.close()
    """
    try:
        statements = get_all_table_ddl() + get_all_index_ddl() + get_all_fts_ddl()
        for stmt in statements:
            conn.execute(stmt)
        conn.commit()

        # Run migrations for backward compatibility (adds missing columns)
        _run_migrations(conn)

        return Success(len(statements))
    except sqlite3.Error as e:
        conn.rollback()
        return Failure(f"Schema application failed: {e}")


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Run schema migrations for backward compatibility.

    Safe to run multiple times - checks for column existence before adding.
    """
    # Migration: Add alias and meta columns to patrons table
    cursor = conn.execute("PRAGMA table_info(patrons)")
    columns = column_names_from_pragma_rows(cursor.fetchall())

    if "alias" not in columns:
        conn.execute("ALTER TABLE patrons ADD COLUMN alias TEXT")

    if "meta" not in columns:
        conn.execute("ALTER TABLE patrons ADD COLUMN meta TEXT")

    # Migration: Add creator_patron_id column to tables table
    cursor = conn.execute("PRAGMA table_info(tables)")
    table_columns = column_names_from_pragma_rows(cursor.fetchall())

    if "creator_patron_id" not in table_columns:
        conn.execute("ALTER TABLE tables ADD COLUMN creator_patron_id TEXT")

    conn.commit()


# @shell_complexity: 4 branches for journal/timeout/fk config parsing
def verify_database_config(conn: sqlite3.Connection) -> Result[dict[str, int | bool | str], str]:
    """
    Verify database configuration (WAL mode, busy_timeout, foreign keys).

    Returns a dict with the configuration values.

    >>> conn = sqlite3.connect(":memory:")
    >>> result = verify_database_config(conn)
    >>> isinstance(result, Success)
    True
    >>> config = result.unwrap()
    >>> isinstance(config['busy_timeout'], int)
    True
    >>> conn.close()
    """
    try:
        journal_result = conn.execute("PRAGMA journal_mode").fetchone()
        timeout_result = conn.execute("PRAGMA busy_timeout").fetchone()
        fk_result = conn.execute("PRAGMA foreign_keys").fetchone()

        journal_mode = normalize_journal_mode(journal_result)
        busy_timeout = normalize_busy_timeout(timeout_result)
        foreign_keys = normalize_foreign_keys_enabled(fk_result)

        return Success(build_database_config(journal_mode, busy_timeout, foreign_keys))
    except sqlite3.Error as e:
        return Failure(f"Failed to verify database config: {e}")


def list_tables(conn: sqlite3.Connection) -> Result[list[str], str]:
    """
    List all tables in the database.

    >>> conn = sqlite3.connect(":memory:")
    >>> result = apply_schema(conn)
    >>> isinstance(result, Success)
    True
    >>> tables = list_tables(conn).unwrap()
    >>> 'patrons' in tables
    True
    >>> 'tables' in tables
    True
    >>> conn.close()
    """
    try:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return Success([row[0] for row in cursor.fetchall()])
    except sqlite3.Error as e:
        return Failure(f"Failed to list tables: {e}")


def list_indexes(conn: sqlite3.Connection) -> Result[list[str], str]:
    """
    List all indexes in the database.

    >>> conn = sqlite3.connect(":memory:")
    >>> result = apply_schema(conn)
    >>> isinstance(result, Success)
    True
    >>> indexes = list_indexes(conn).unwrap()
    >>> len(indexes) >= 6  # At least our 6 indexes
    True
    >>> conn.close()
    """
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%' ORDER BY name"
        )
        return Success([row[0] for row in cursor.fetchall()])
    except sqlite3.Error as e:
        return Failure(f"Failed to list indexes: {e}")
