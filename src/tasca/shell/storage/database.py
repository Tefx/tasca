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
    create_saying_attachments_table_ddl,
    get_all_fts_ddl,
    get_all_index_ddl,
    get_all_table_ddl,
)

# Default busy timeout in milliseconds
DEFAULT_BUSY_TIMEOUT = 5000


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
        db_path_value = str(db_path)
        _ensure_database_directory(db_path, db_path_value)
        conn = sqlite3.connect(db_path_value)
        _configure_connection_defaults(conn)
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
    >>> result.unwrap()  # 7 tables + 8 indexes + 4 FTS = 19 statements
    19
    >>> tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    >>> 'patrons' in [t[0] for t in tables]
    True
    >>> 'sayings' in [t[0] for t in tables]
    True
    >>> 'saying_attachments' in [t[0] for t in tables]
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

    if "host_ids" not in table_columns:
        conn.execute("ALTER TABLE tables ADD COLUMN host_ids TEXT")

    if "metadata" not in table_columns:
        conn.execute("ALTER TABLE tables ADD COLUMN metadata TEXT")

    if "policy" not in table_columns:
        conn.execute("ALTER TABLE tables ADD COLUMN policy TEXT")

    if "board" not in table_columns:
        conn.execute("ALTER TABLE tables ADD COLUMN board TEXT")

    _migrate_saying_attachments_to_allow_empty_content(conn)
    conn.commit()


# @shell_orchestration: transactional SQLite table rebuild for a legacy CHECK constraint
def _migrate_saying_attachments_to_allow_empty_content(conn: sqlite3.Connection) -> None:
    """Replace the pre-release positive-byte CHECK while preserving attachment rows."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'saying_attachments'"
    ).fetchone()
    if row is None or row[0] is None:
        return
    normalized_ddl = "".join(str(row[0]).lower().split())
    if "check(byte_size>=1)" not in normalized_ddl:
        return

    conn.execute(
        "ALTER TABLE saying_attachments RENAME TO saying_attachments_nonempty_legacy"
    )
    conn.execute(create_saying_attachments_table_ddl())
    conn.execute("""
        INSERT INTO saying_attachments (
            id, saying_id, position, name, content, byte_size
        )
        SELECT id, saying_id, position, name, content, byte_size
        FROM saying_attachments_nonempty_legacy
        """)
    conn.execute("DROP TABLE saying_attachments_nonempty_legacy")


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
        return _read_database_config(conn)
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


def _ensure_database_directory(db_path: Path, db_path_value: str) -> None:
    """Create database parent directory unless using :memory:."""
    if not is_memory_database_path(db_path_value):
        db_path.parent.mkdir(parents=True, exist_ok=True)


def _configure_connection_defaults(conn: sqlite3.Connection) -> None:
    """Apply default SQLite pragmas for Tasca runtime."""
    conn.execute("PRAGMA journal_mode=WAL").fetchone()
    conn.execute(f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT}")
    conn.execute("PRAGMA foreign_keys=ON")


def _read_database_config(conn: sqlite3.Connection) -> Result[dict[str, int | bool | str], str]:
    """Read and normalize journal, busy_timeout, and foreign_keys pragmas."""
    journal_result = conn.execute("PRAGMA journal_mode").fetchone()
    timeout_result = conn.execute("PRAGMA busy_timeout").fetchone()
    fk_result = conn.execute("PRAGMA foreign_keys").fetchone()

    journal_mode = normalize_journal_mode(journal_result)
    busy_timeout = normalize_busy_timeout(timeout_result)
    foreign_keys = normalize_foreign_keys_enabled(fk_result)
    return Success(build_database_config(journal_mode, busy_timeout, foreign_keys))
