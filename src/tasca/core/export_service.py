"""
Export service - pure functions for generating export formats.

This module provides pure functions for transforming Table and Saying data
into exportable string formats (JSONL, Markdown). All I/O operations are
handled by the shell layer (API routes, repositories).

Design Decisions:
    - Input: Table + list[Saying] (domain types)
    - Output: str (formatted content)
    - No I/O: Pure transformations only
    - Contracts: @deal.pre/@deal.post for invariants
    - Full content: No truncation of saying content
"""

from datetime import datetime
from typing import Any

import deal
from pydantic import BaseModel

from tasca.core.domain.saying import Saying, SpeakerKind
from tasca.core.domain.table import Table

# =============================================================================
# JSONL Export Types (re-exported from domain for export format)
# =============================================================================


class ExportHeader(BaseModel):
    """Header line for JSONL export.

    This is the first line in a JSONL export file, providing metadata
    about the export.

    Attributes:
        type: Always "export_header".
        export_version: Export format version.
        exported_at: ISO timestamp of when export was created.
        table_id: ID of the exported table.
    """

    type: str = "export_header"
    export_version: str = "0.1"
    exported_at: str
    table_id: str


class TableExport(BaseModel):
    """Table snapshot for export.

    Attributes:
        type: Always "table".
        table: The table data.
    """

    type: str = "table"
    table: dict[str, Any]


class SayingExport(BaseModel):
    """Saying entry for export.

    Attributes:
        type: Always "saying".
        saying: The saying data.
    """

    type: str = "saying"
    saying: dict[str, Any]


# =============================================================================
# JSONL Export
# =============================================================================


@deal.pre(
    lambda table, sayings, exported_at: (
        table is not None
        and sayings is not None
        and exported_at is not None
        and len(exported_at) > 0
        and isinstance(exported_at, str)
    )
)
@deal.post(lambda result: isinstance(result, str))
@deal.post(lambda result: len(result) > 0)
@deal.ensure(
    lambda table, sayings, exported_at, result: result.startswith('{"type":"export_header"')
)
def generate_jsonl(table: Table, sayings: list[Saying], exported_at: str) -> str:
    """Generate JSONL export string for a table and its sayings.

    JSONL format:
        - Line 1: export_header with metadata
        - Line 2: table snapshot
        - Line 3+: sayings ordered by sequence

    Args:
        table: The table to export (required, non-null).
        sayings: List of sayings for this table (may be empty, ordered by sequence).
        exported_at: ISO timestamp for the export (required).

    Returns:
        JSONL string with header, table, and saying lines.

    Examples:
        >>> from datetime import datetime, timezone
        >>> from tasca.core.domain.table import Table, TableId, TableStatus, Version
        >>> from tasca.core.domain.saying import Saying, SayingId, Speaker, SpeakerKind
        >>> from tasca.core.domain.patron import PatronId
        >>> t = Table(
        ...     id=TableId("t-001"),
        ...     question="Test question?",
        ...     context="Test context",
        ...     status=TableStatus.OPEN,
        ...     version=Version(1),
        ...     created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ...     updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ... )
        >>> result = generate_jsonl(t, [], "2024-01-01T00:00:00Z")
        >>> lines = result.split("\\n")
        >>> len(lines)
        2
        >>> '"type":"export_header"' in lines[0]
        True
        >>> '"type":"table"' in lines[1]
        True

        >>> s1 = Saying(
        ...     id=SayingId("s-001"),
        ...     table_id="t-001",
        ...     sequence=0,
        ...     speaker=Speaker(kind=SpeakerKind.AGENT, name="Bot", patron_id=PatronId("p-001")),
        ...     content="Hello world",
        ...     created_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        ... )
        >>> result2 = generate_jsonl(t, [s1], "2024-01-01T00:00:00Z")
        >>> lines2 = result2.split("\\n")
        >>> len(lines2)
        3
        >>> '"type":"saying"' in lines2[2]
        True
    """
    lines: list[str] = []

    # Header line
    header = ExportHeader(
        exported_at=exported_at,
        table_id=table.id,
    )
    lines.append(header.model_dump_json())

    # Table line
    table_export = TableExport(
        table={
            "id": table.id,
            "question": table.question,
            "context": table.context,
            "status": table.status.value,
            "version": table.version,
            "created_at": table.created_at.isoformat(),
            "updated_at": table.updated_at.isoformat(),
        }
    )
    lines.append(table_export.model_dump_json())

    # Sayings (in order received - caller should sort by sequence)
    for saying in sayings:
        saying_export = SayingExport(
            saying={
                "id": saying.id,
                "table_id": saying.table_id,
                "sequence": saying.sequence,
                "speaker": {
                    "kind": saying.speaker.kind.value,
                    "name": saying.speaker.name,
                    "patron_id": saying.speaker.patron_id,
                },
                "content": saying.content,
                "pinned": saying.pinned,
                "created_at": saying.created_at.isoformat(),
            }
        )
        lines.append(saying_export.model_dump_json())

    return "\n".join(lines)


# =============================================================================
# Markdown Export
# =============================================================================


@deal.pre(lambda dt: dt is not None)
@deal.post(lambda result: isinstance(result, str) and "UTC" in result)
def _fmt_dt(dt: datetime) -> str:
    """Format a datetime as human-readable UTC string.

    Args:
        dt: Datetime to format.

    Returns:
        Human-readable string like "2024-01-01 12:00 UTC".

    Examples:
        >>> from datetime import datetime, timezone
        >>> _fmt_dt(datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc))
        '2024-01-15 14:30 UTC'
    """
    return dt.strftime("%Y-%m-%d %H:%M") + " UTC"


@deal.pre(
    lambda table, sayings, board=None: (
        table is not None and sayings is not None and (board is None or isinstance(board, dict))
    )
)
@deal.post(lambda result: isinstance(result, str))
@deal.post(lambda result: len(result) > 0)
@deal.ensure(lambda *args, result, **kwargs: (args[0] if args else kwargs["table"]).question in result)
@deal.ensure(lambda *args, result, **kwargs: str((args[0] if args else kwargs["table"]).id) in result)
def generate_markdown(table: Table, sayings: list[Saying], board: dict[str, Any] | None = None) -> str:
    """Generate Markdown export string for a table and its sayings.

    Markdown format:
        - Title header with question
        - Metadata block (table_id, status, version, timestamps)
        - Context section (if present)
        - Board section in stable order (agenda, summary, decision_draft, then others)
        - Transcript section with stable ``[seq=...] timestamp (speaker): content`` lines

    Note:
        Saying content is NOT truncated. Full content is preserved.

    Args:
        table: The table to export (required, non-null).
        sayings: List of sayings for this table (may be empty, ordered by sequence).
        board: Optional board snapshot. Missing board exports as an empty section.

    Returns:
        Markdown string with table metadata and transcript.

    Examples:
        >>> from datetime import datetime, timezone
        >>> from tasca.core.domain.table import Table, TableId, TableStatus, Version
        >>> from tasca.core.domain.saying import Saying, SayingId, Speaker, SpeakerKind
        >>> from tasca.core.domain.patron import PatronId
        >>> t = Table(
        ...     id=TableId("t-001"),
        ...     question="What is AI?",
        ...     context="Discussion about AI",
        ...     status=TableStatus.OPEN,
        ...     version=Version(1),
        ...     created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ...     updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ... )
        >>> result = generate_markdown(t, [])
        >>> "# What is AI?" in result
        True
        >>> "- table_id: t-001" in result
        True
        >>> "## Board" in result
        True
        >>> "_No sayings yet._" in result
        True

        >>> s1 = Saying(
        ...     id=SayingId("s-001"),
        ...     table_id="t-001",
        ...     sequence=0,
        ...     speaker=Speaker(kind=SpeakerKind.HUMAN, name="Alice"),
        ...     content="Full content preserved without truncation.",
        ...     created_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        ... )
        >>> result2 = generate_markdown(t, [s1])
        >>> "Full content preserved without truncation." in result2
        True
        >>> "- [seq=0] 2024-01-01T12:00:00+00:00 (human:Alice): Full content preserved without truncation." in result2
        True

        >>> s2 = Saying(
        ...     id=SayingId("s-002"),
        ...     table_id="t-001",
        ...     sequence=1,
        ...     speaker=Speaker(kind=SpeakerKind.AGENT, name="Bot", patron_id=PatronId("p-1")),
        ...     content="Agent reply.",
        ...     pinned=True,
        ...     created_at=datetime(2024, 1, 1, 12, 5, tzinfo=timezone.utc),
        ... )
        >>> result3 = generate_markdown(t, [s1, s2])
        >>> "(agent:Bot): Agent reply." in result3
        True
        >>> "[pinned]" in result3
        True
        >>> generate_markdown(t, [], {"z": "last", "agenda": "first"}).split("## Board")[1].splitlines()[2]
        '### agenda'
    """
    lines: list[str] = []

    # Title
    lines.append(f"# {table.question}")
    lines.append("")

    # Metadata block follows the technical design/search-export template.
    lines.append(f"- table_id: {table.id}")
    lines.append(f"- status: {table.status.value}")
    lines.append(f"- creator: {table.creator_patron_id or ''}")
    lines.append("- hosts: ")
    lines.append(f"- created_at: {table.created_at.isoformat()}")
    lines.append("- tags/space: ")
    lines.append("")

    # Context section (blockquote to signal "background/framing")
    if table.context:
        lines.append("## Context")
        lines.append("")
        # Blockquote each line of context
        for ctx_line in table.context.split("\n"):
            lines.append(f"> {ctx_line}" if ctx_line.strip() else ">")
        lines.append("")

    # Board section (stable order: agenda, summary, decision_draft, then others)
    lines.append("## Board")
    lines.append("")
    board_snapshot = board if board is not None else {}
    ordered_keys = [key for key in ("agenda", "summary", "decision_draft") if key in board_snapshot]
    ordered_keys.extend(sorted(key for key in board_snapshot if key not in set(ordered_keys)))
    if not ordered_keys:
        lines.append("_No board entries._")
        lines.append("")
    else:
        for key in ordered_keys:
            lines.append(f"### {key}")
            lines.append(str(board_snapshot[key]))
            lines.append("")

    # Transcript section
    lines.append("## Transcript")
    lines.append("")

    if not sayings:
        lines.append("_No sayings yet._")
    else:
        for saying in sayings:
            seq = saying.sequence
            name = saying.speaker.name
            speaker_label = (
                "human" if saying.speaker.kind == SpeakerKind.HUMAN else f"agent:{name}"
            )
            if saying.speaker.kind == SpeakerKind.HUMAN and name:
                speaker_label = f"human:{name}"
            time_str = saying.created_at.isoformat()
            pin_tag = " [pinned]" if saying.pinned else ""
            lines.append(f"- [seq={seq}] {time_str} ({speaker_label}): {saying.content}{pin_tag}")

    return "\n".join(lines)
