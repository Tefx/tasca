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
from tasca.core.domain.saying import (
    AttachmentId,
    AttachmentSummary,
    Saying,
    SayingAttachment,
    SayingId,
    Speaker,
    SpeakerKind,
)
from tasca.core.domain.table import Table, TableId, TableStatus, Version

JsonObject = dict[str, object]


@deal.pre(lambda raw: isinstance(raw, str))
@deal.post(lambda result: isinstance(result, bool))
def _is_table_status(raw: object) -> bool:
    """Return whether a raw database value is a valid table status.

    Examples:
        >>> _is_table_status("open")
        True
        >>> _is_table_status("missing")
        False
    """
    return raw in {status.value for status in TableStatus}


@deal.pre(lambda raw: isinstance(raw, str | int))
@deal.post(lambda result: isinstance(result, bool))
def _is_sqlite_int(raw: object) -> bool:
    """Return whether a raw database value can be decoded as an integer.

    Examples:
        >>> _is_sqlite_int("12")
        True
        >>> _is_sqlite_int("not-an-int")
        False
    """
    try:
        int(cast(Any, raw))
    except (TypeError, ValueError):
        return False
    return True


@deal.pre(lambda raw: isinstance(raw, str | datetime))
@deal.post(lambda result: isinstance(result, bool))
def _is_iso_datetime(raw: object) -> bool:
    """Return whether a raw database value can be decoded as an ISO datetime.

    Examples:
        >>> _is_iso_datetime("2026-01-01T00:00:00")
        True
        >>> _is_iso_datetime("not-a-date")
        False
    """
    try:
        datetime.fromisoformat(str(raw))
    except ValueError:
        return False
    return True


@deal.pre(lambda host_ids: all(isinstance(item, str) for item in host_ids))
@deal.post(lambda result: isinstance(result, str))
def encode_host_ids(host_ids: list[str]) -> str:
    """Encode host IDs for SQLite storage.

    Examples:
        >>> encode_host_ids(["p1", "p2"])
        '["p1", "p2"]'
    """
    return json.dumps(host_ids)


@deal.post(lambda result: isinstance(result, bool))
def _is_json_serializable_value(value: object) -> bool:
    """Return whether a value fits SQLite JSON object storage.

    Examples:
        >>> _is_json_serializable_value({"a": [1, None, True]})
        True
        >>> _is_json_serializable_value({"bad": object()})
        False
    """
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_serializable_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_serializable_value(item) for key, item in value.items())
    return False


@deal.pre(lambda value: isinstance(value, dict) and _is_json_serializable_value(value))
@deal.post(lambda result: isinstance(result, str))
def encode_table_json_object(value: JsonObject) -> str:
    """Encode table metadata-like JSON objects for SQLite storage.

    Examples:
        >>> encode_table_json_object({"a": 1})
        '{"a": 1}'
    """
    return json.dumps(value)


@deal.pre(lambda raw: raw is None or isinstance(raw, str))
@deal.post(lambda result: isinstance(result, dict))
def decode_table_json_object(raw: str | None) -> JsonObject:
    """Decode table metadata-like JSON, falling back to empty for legacy rows.

    Examples:
        >>> decode_table_json_object(None)
        {}
        >>> decode_table_json_object('{"a": 1}')
        {'a': 1}
        >>> decode_table_json_object('["not", "an", "object"]')
        {}
    """
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict) or not _is_json_serializable_value(decoded):
        return {}
    return decoded


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


@deal.pre(
    lambda row: len(row) >= 7
    and isinstance(row[3], str)
    and _is_table_status(row[3])
    and isinstance(row[4], str | int)
    and _is_sqlite_int(row[4])
    and isinstance(row[5], str | datetime)
    and _is_iso_datetime(row[5])
    and isinstance(row[6], str | datetime)
    and _is_iso_datetime(row[6])
)
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
        metadata=decode_table_json_object(str(row[9]) if len(row) > 9 and row[9] is not None else None),
        policy=decode_table_json_object(str(row[10]) if len(row) > 10 and row[10] is not None else None),
        board=decode_table_json_object(str(row[11]) if len(row) > 11 and row[11] is not None else None),
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
    lambda row: len(row) >= 5
    and all(isinstance(row[index], str) for index in (0, 1, 2))
    and all(isinstance(row[index], str | int) for index in (3, 4))
    and all(_is_sqlite_int(row[index]) for index in (3, 4))
    and int(cast(Any, row[3])) >= 0
    and int(cast(Any, row[4])) >= 1
)
@deal.post(lambda result: isinstance(result, AttachmentSummary))
def row_to_attachment_summary(row: tuple[object, ...]) -> AttachmentSummary:
    """Convert a metadata-only attachment row without requiring content.

    >>> row_to_attachment_summary(("a1", "s1", "notes.md", 0, 7)).byte_size
    7
    >>> row_to_attachment_summary(("a2", "s1", "one.md", 1, 1)).byte_size
    1
    """
    return AttachmentSummary(
        id=AttachmentId(str(row[0])),
        name=str(row[2]),
        position=int(cast(Any, row[3])),
        byte_size=int(cast(Any, row[4])),
    )


@deal.pre(
    lambda row: len(row) >= 7
    and all(isinstance(row[index], str) for index in (0, 1, 2, 3, 6))
    and all(isinstance(row[index], str | int) for index in (4, 5))
    and all(_is_sqlite_int(row[index]) for index in (4, 5))
    and int(cast(Any, row[4])) >= 0
    and int(cast(Any, row[5])) >= 1
)
@deal.post(lambda result: isinstance(result, SayingAttachment))
def row_to_attachment(row: tuple[object, ...]) -> SayingAttachment:
    """Convert a complete attachment row while preserving Markdown content.

    >>> row_to_attachment(("a1", "s1", "t1", "notes.md", 0, 7, "# note")).content
    '# note'
    >>> row_to_attachment(("a2", "s1", "t1", "one.md", 1, 1, "x")).content
    'x'
    """
    return SayingAttachment(
        id=AttachmentId(str(row[0])),
        saying_id=SayingId(str(row[1])),
        table_id=str(row[2]),
        name=str(row[3]),
        position=int(cast(Any, row[4])),
        byte_size=int(cast(Any, row[5])),
        content=str(row[6]),
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
