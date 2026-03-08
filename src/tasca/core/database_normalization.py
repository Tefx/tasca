"""Pure helpers for SQLite config and pragma value normalization."""

import deal

from tasca.core.schema import is_valid_busy_timeout, is_wal_mode

PragmaRow = tuple[int | str | None, ...]


@deal.post(lambda result: isinstance(result, bool))
def is_memory_database_path(db_path: str) -> bool:
    """Return whether the SQLite path targets in-memory storage.

    >>> is_memory_database_path(":memory:")
    True
    >>> is_memory_database_path("./tasca.db")
    False
    """
    return db_path == ":memory:"


@deal.pre(
    lambda journal_row: (
        journal_row is None
        or (len(journal_row) > 0 and journal_row[0] is not None and len(str(journal_row[0])) > 0)
    )
)
@deal.post(lambda result: isinstance(result, str) and len(result) > 0)
def normalize_journal_mode(journal_row: PragmaRow | None) -> str:
    """Normalize PRAGMA journal_mode row into a journal mode string.

    >>> normalize_journal_mode(("wal",))
    'wal'
    >>> normalize_journal_mode(None)
    'unknown'
    """
    if journal_row is None:
        return "unknown"
    return str(journal_row[0])


@deal.pre(
    lambda timeout_row: (
        timeout_row is None
        or (
            len(timeout_row) > 0
            and (
                (isinstance(timeout_row[0], int) and timeout_row[0] >= 0)
                or (isinstance(timeout_row[0], str) and timeout_row[0].isdigit())
            )
        )
    )
)
@deal.post(lambda result: result >= 0)
def normalize_busy_timeout(timeout_row: PragmaRow | None) -> int:
    """Normalize PRAGMA busy_timeout row into an integer timeout.

    >>> normalize_busy_timeout((5000,))
    5000
    >>> normalize_busy_timeout(None)
    0
    """
    if timeout_row is None:
        return 0
    timeout_value = timeout_row[0]
    if isinstance(timeout_value, int):
        return timeout_value
    if isinstance(timeout_value, str):
        return int(timeout_value)
    return 0


@deal.pre(lambda foreign_keys_row: foreign_keys_row is None or len(foreign_keys_row) > 0)
@deal.post(lambda result: isinstance(result, bool))
def normalize_foreign_keys_enabled(foreign_keys_row: PragmaRow | None) -> bool:
    """Normalize PRAGMA foreign_keys row into an enabled flag.

    >>> normalize_foreign_keys_enabled((1,))
    True
    >>> normalize_foreign_keys_enabled(None)
    False
    """
    if foreign_keys_row is None:
        return False
    return foreign_keys_row[0] == 1


@deal.pre(
    lambda journal_mode, busy_timeout, foreign_keys_enabled: (
        len(journal_mode) > 0 and busy_timeout >= 0 and isinstance(foreign_keys_enabled, bool)
    )
)
def build_database_config(
    journal_mode: str,
    busy_timeout: int,
    foreign_keys_enabled: bool,
) -> dict[str, int | bool | str]:
    """Build normalized database config map.

    >>> build_database_config("wal", 5000, True)["wal_mode_enabled"]
    True
    >>> build_database_config("memory", 0, False)["busy_timeout_valid"]
    False
    """
    return {
        "journal_mode": journal_mode,
        "wal_mode_enabled": is_wal_mode(journal_mode),
        "busy_timeout": busy_timeout,
        "busy_timeout_valid": is_valid_busy_timeout(busy_timeout),
        "foreign_keys_enabled": foreign_keys_enabled,
    }


@deal.pre(lambda rows: all(len(row) > 1 for row in rows))
@deal.post(lambda result: all(isinstance(name, str) for name in result))
def column_names_from_pragma_rows(rows: list[tuple[int | str | None, ...]]) -> set[str]:
    """Extract column names from PRAGMA table_info rows.

    >>> rows = [(0, "id", "TEXT", 0, None, 1), (1, "alias", "TEXT", 0, None, 0)]
    >>> sorted(column_names_from_pragma_rows(rows))
    ['alias', 'id']
    """
    return {str(row[1]) for row in rows}
