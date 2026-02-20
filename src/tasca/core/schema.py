"""
SQLite schema definitions - Pure DDL generation.

This module provides functions for generating SQLite DDL statements.
All functions are pure (no I/O) and use @pre/@post contracts.
"""

import deal


@deal.post(lambda result: len(result) > 0)  # DDL string is non-empty
def create_patrons_table_ddl(table_name: str = "patrons") -> str:
    """
    Generate DDL for the patrons table.

    >>> "patrons" in create_patrons_table_ddl()
    True
    >>> "id TEXT PRIMARY KEY" in create_patrons_table_ddl()
    True
    >>> 'kind TEXT NOT NULL DEFAULT' in create_patrons_table_ddl()
    True
    """
    return f"""CREATE TABLE IF NOT EXISTS {table_name} (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'agent',
    created_at TEXT NOT NULL
)"""


@deal.post(lambda result: len(result) > 0)  # DDL string is non-empty
def create_tables_table_ddl(table_name: str = "tables") -> str:
    """
    Generate DDL for the tables table.

    >>> "tables" in create_tables_table_ddl()
    True
    >>> "id TEXT PRIMARY KEY" in create_tables_table_ddl()
    True
    >>> "status TEXT NOT NULL DEFAULT" in create_tables_table_ddl()
    True
    """
    return f"""CREATE TABLE IF NOT EXISTS {table_name} (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    context TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)"""


@deal.post(lambda result: len(result) > 0)  # DDL string is non-empty
def create_seats_table_ddl(table_name: str = "seats") -> str:
    """
    Generate DDL for the seats table.

    >>> "seats" in create_seats_table_ddl()
    True
    >>> "FOREIGN KEY (table_id)" in create_seats_table_ddl()
    True
    >>> "FOREIGN KEY (patron_id)" in create_seats_table_ddl()
    True
    """
    return f"""CREATE TABLE IF NOT EXISTS {table_name} (
    id TEXT PRIMARY KEY,
    table_id TEXT NOT NULL,
    patron_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'joined',
    last_heartbeat TEXT NOT NULL,
    joined_at TEXT NOT NULL,
    FOREIGN KEY (table_id) REFERENCES tables(id),
    FOREIGN KEY (patron_id) REFERENCES patrons(id)
)"""


@deal.post(lambda result: len(result) > 0)  # DDL string is non-empty
def create_sayings_table_ddl(table_name: str = "sayings") -> str:
    """
    Generate DDL for the sayings table.

    The sequence column provides:
    - Unique (table_id, sequence) tuple for each saying
    - Monotonically increasing per table
    - Ordered replay of discussion history

    >>> "sayings" in create_sayings_table_ddl()
    True
    >>> "speaker_kind TEXT NOT NULL" in create_sayings_table_ddl()
    True
    >>> "FOREIGN KEY (table_id)" in create_sayings_table_ddl()
    True
    >>> "UNIQUE(table_id, sequence)" in create_sayings_table_ddl()
    True
    """
    return f"""CREATE TABLE IF NOT EXISTS {table_name} (
    id TEXT PRIMARY KEY,
    table_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    speaker_kind TEXT NOT NULL,
    speaker_name TEXT NOT NULL,
    speaker_id TEXT,
    content TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (table_id) REFERENCES tables(id),
    UNIQUE(table_id, sequence)
)"""


@deal.post(lambda result: len(result) > 0)  # DDL string is non-empty
def create_dedup_table_ddl(table_name: str = "dedup") -> str:
    """
    Generate DDL for the dedup (deduplication) table.

    >>> "dedup" in create_dedup_table_ddl()
    True
    >>> "content_hash TEXT PRIMARY KEY" in create_dedup_table_ddl()
    True
    """
    return f"""CREATE TABLE IF NOT EXISTS {table_name} (
    content_hash TEXT PRIMARY KEY,
    content_preview TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
)"""


@deal.post(lambda result: len(result) == 5)
def get_all_table_ddl() -> list[str]:
    """
    Get all table creation DDL statements in dependency order.

    >>> len(get_all_table_ddl())
    5
    >>> get_all_table_ddl()[0].startswith("CREATE TABLE IF NOT EXISTS patrons")
    True
    """
    return [
        create_patrons_table_ddl(),
        create_tables_table_ddl(),
        create_seats_table_ddl(),
        create_sayings_table_ddl(),
        create_dedup_table_ddl(),
    ]


@deal.pre(
    lambda index_name, table_name, columns: (
        len(index_name) > 0 and len(table_name) > 0 and len(columns) > 0
    )
)
def create_index_ddl(index_name: str, table_name: str, columns: list[str]) -> str:
    """
    Generate DDL for an index.

    >>> "idx_seats_table_id" in create_index_ddl("idx_seats_table_id", "seats", ["table_id"])
    True
    >>> "col1" in create_index_ddl("idx_test", "test", ["col1", "col2"])
    True
    """
    cols = ", ".join(columns)
    return f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name}({cols})"


@deal.post(lambda result: len(result) == 7)
def get_all_index_ddl() -> list[str]:
    """
    Get all index creation DDL statements.

    >>> len(get_all_index_ddl())
    7
    >>> any("idx_seats_table_id" in idx for idx in get_all_index_ddl())
    True
    >>> any("idx_sayings_table_sequence" in idx for idx in get_all_index_ddl())
    True
    """
    return [
        # Seats indexes
        create_index_ddl("idx_seats_table_id", "seats", ["table_id"]),
        create_index_ddl("idx_seats_patron_id", "seats", ["patron_id"]),
        # Sayings indexes
        create_index_ddl("idx_sayings_table_id", "sayings", ["table_id"]),
        create_index_ddl("idx_sayings_table_sequence", "sayings", ["table_id", "sequence"]),
        create_index_ddl("idx_sayings_created_at", "sayings", ["created_at"]),
        # Tables indexes
        create_index_ddl("idx_tables_status", "tables", ["status"]),
        # Dedup index
        create_index_ddl("idx_dedup_first_seen", "dedup", ["first_seen_at"]),
    ]


@deal.pre(lambda journal_mode: len(journal_mode) > 0)
def is_wal_mode(journal_mode: str) -> bool:
    """
    Check if the journal mode indicates WAL is enabled.

    >>> is_wal_mode("WAL")
    True
    >>> is_wal_mode("wal")
    True
    >>> is_wal_mode("memory")
    False
    >>> is_wal_mode("delete")
    False
    """
    return journal_mode.upper() == "WAL"


@deal.post(lambda result: isinstance(result, bool))
def is_valid_busy_timeout(value: int) -> bool:
    """
    Check if busy_timeout value is valid and reasonable.

    Valid values are positive integers. We consider >= 1000ms reasonable.

    >>> is_valid_busy_timeout(5000)
    True
    >>> is_valid_busy_timeout(1000)
    True
    >>> is_valid_busy_timeout(999)
    False
    >>> is_valid_busy_timeout(0)
    False
    >>> is_valid_busy_timeout(-1)
    False
    """
    return value >= 1000
