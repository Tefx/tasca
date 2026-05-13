"""Pure row adapters shared by SQLite shell repositories.

The functions in this module perform no I/O; they normalize already-fetched
row values into domain objects while preserving legacy fallback semantics.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, cast

import deal

from tasca.core.domain.patron import PatronId
from tasca.core.domain.saying import Saying, SayingId, Speaker, SpeakerKind
from tasca.core.domain.table import Table, TableId, TableStatus, Version


@deal.pre(lambda host_ids: all(isinstance(item, str) for item in host_ids))
@deal.post(lambda result: isinstance(result, str))
def encode_host_ids(host_ids: list[str]) -> str:
    """Encode host IDs for SQLite storage.

    Examples:
        >>> encode_host_ids(["p1", "p2"])
        '["p1", "p2"]'
    """
    return json.dumps(host_ids)


@deal.pre(lambda raw: raw is None or isinstance(raw, str))
@deal.post(lambda result: all(isinstance(item, str) for item in result))
def decode_host_ids(raw: str | None) -> list[str]:
    """Decode host IDs, treating missing or malformed legacy values as empty.

    Examples:
        >>> decode_host_ids(None)
        []
        >>> decode_host_ids('["p1", 2, "p3"]')
        ['p1', 'p3']
        >>> decode_host_ids('{bad json')
        []
    """
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, list):
        return []
    return [item for item in decoded if isinstance(item, str)]


@deal.pre(lambda row: len(row) >= 7)
@deal.post(lambda result: isinstance(result, Table))
def row_to_table(row: tuple[object, ...]) -> Table:
    """Convert a table database row to a domain object.

    Examples:
        >>> row = ("t1", "Question?", None, "open", 1, "2026-01-01T00:00:00", "2026-01-01T00:00:00")
        >>> row_to_table(row).id
        't1'
    """
    return Table(
        id=TableId(str(row[0])),
        question=str(row[1]),
        context=str(row[2]) if row[2] is not None else None,
        status=TableStatus(str(row[3])),
        version=Version(int(cast(Any, row[4]))),
        created_at=datetime.fromisoformat(str(row[5])),
        updated_at=datetime.fromisoformat(str(row[6])),
        creator_patron_id=str(row[7]) if len(row) > 7 and row[7] is not None else None,
        host_ids=decode_host_ids(str(row[8]) if len(row) > 8 and row[8] is not None else None),
    )


@deal.pre(lambda row: len(row) >= 9)
@deal.post(lambda result: isinstance(result, Saying))
def row_to_saying(row: tuple[object, ...]) -> Saying:
    """Convert a saying database row to a domain object.

    Examples:
        >>> row = ("s1", "t1", 0, "agent", "Agent", None, "hello", 0, "2026-01-01T00:00:00")
        >>> row_to_saying(row).content
        'hello'
    """
    patron_raw = row[5]
    return Saying(
        id=SayingId(str(row[0])),
        table_id=str(row[1]),
        sequence=int(cast(Any, row[2])),
        speaker=Speaker(
            kind=SpeakerKind(str(row[3])),
            name=str(row[4]),
            patron_id=PatronId(str(patron_raw)) if patron_raw else None,
        ),
        content=str(row[6]),
        pinned=bool(row[7]),
        created_at=datetime.fromisoformat(str(row[8])),
    )


@deal.pre(
    lambda text, query, max_len=200: isinstance(text, str)
    and isinstance(query, str)
    and max_len >= 0
)
@deal.post(lambda result: isinstance(result, str))
def truncate_snippet(text: str, query: str, max_len: int = 200) -> str:
    """Truncate text around a query match for snippet display.

    Examples:
        >>> truncate_snippet("short text", "text")
        'short text'
        >>> truncate_snippet("a" * 10 + "needle" + "b" * 10, "needle", 8)
        '...aaaaaaaaaaneedlebbbbbbbbbb'
    """
    if len(text) <= max_len:
        return text

    idx = text.lower().find(query.lower())
    if idx < 0:
        return text[:max_len] + "..."

    start = max(0, idx - 50)
    end = min(len(text), idx + len(query) + 50)

    result = text[start:end]
    if start > 0:
        result = "..." + result
    if end < len(text):
        result = result + "..."

    return result
