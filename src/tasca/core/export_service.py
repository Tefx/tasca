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

import json
from datetime import datetime, timezone

import deal
from pydantic import BaseModel

from tasca.core.domain.saying import Saying
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
    table: dict


class SayingExport(BaseModel):
    """Saying entry for export.

    Attributes:
        type: Always "saying".
        saying: The saying data.
    """

    type: str = "saying"
    saying: dict


# =============================================================================
# JSONL Export
# =============================================================================


@deal.pre(lambda table, sayings, exported_at: exported_at is not None and len(exported_at) > 0)
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


@deal.pre(lambda table, sayings: table is not None)
@deal.post(lambda result: isinstance(result, str))
@deal.post(lambda result: len(result) > 0)
@deal.ensure(lambda table, sayings, result: table.question in result)
@deal.ensure(lambda table, sayings, result: str(table.id) in result)
def generate_markdown(table: Table, sayings: list[Saying]) -> str:
    """Generate Markdown export string for a table and its sayings.

    Markdown format:
        - Title header with question
        - Metadata section (table_id, status, version, timestamps, context)
        - Board section (placeholder)
        - Transcript section with sayings (FULL content, no truncation)

    Note:
        Saying content is NOT truncated. Full content is preserved.

    Args:
        table: The table to export (required, non-null).
        sayings: List of sayings for this table (may be empty, ordered by sequence).

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
        >>> "table_id: t-001" in result
        True
        >>> "_No sayings yet._" in result
        True

        >>> s1 = Saying(
        ...     id=SayingId("s-001"),
        ...     table_id="t-001",
        ...     sequence=0,
        ...     speaker=Speaker(kind=SpeakerKind.HUMAN, name="Alice"),
        ...     content="This is a very long content that would have been truncated to 200 chars but now is preserved in full.",
        ...     created_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        ... )
        >>> result2 = generate_markdown(t, [s1])
        >>> "This is a very long content that would have been truncated to 200 chars but now is preserved in full." in result2
        True
        >>> "..." not in result2.split("Transcript")[1].split("\\n")[1]  # No truncation marker
        True
        >>> "[seq=0]" in result2
        True
        >>> "human:Alice" in result2
        True
    """
    lines: list[str] = []

    # Title
    lines.append(f"# {table.question}")
    lines.append("")

    # Metadata
    lines.append(f"- table_id: {table.id}")
    lines.append(f"- status: {table.status.value}")
    lines.append(f"- version: {table.version}")
    lines.append(f"- created_at: {table.created_at.isoformat()}")
    lines.append(f"- updated_at: {table.updated_at.isoformat()}")
    if table.context:
        lines.append(f"- context: {table.context}")
    lines.append("")

    # Board section (placeholder - no board data yet)
    lines.append("## Board")
    lines.append("")
    lines.append("_No board data available._")
    lines.append("")

    # Transcript section
    lines.append("## Transcript")
    lines.append("")

    if not sayings:
        lines.append("_No sayings yet._")
    else:
        for saying in sayings:
            # Format: - [seq=N] TIMESTAMP (SPEAKER_KIND:NAME): content
            speaker_prefix = f"{saying.speaker.kind.value}:{saying.speaker.name}"
            timestamp = saying.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
            # Full content - NO truncation (per task requirement)
            content = saying.content
            lines.append(f"- [seq={saying.sequence}] {timestamp} ({speaker_prefix}): {content}")

    return "\n".join(lines)
