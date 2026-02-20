"""
Saying domain types.

This module defines the core Saying types for messages in discussions.
Atomic sequence allocation ensures each saying has a unique (table_id, sequence) tuple.
"""

from datetime import datetime
from enum import Enum
from typing import NewType

from pydantic import BaseModel, Field


class SpeakerKind(str, Enum):
    """Kind of speaker in a discussion."""

    AGENT = "agent"
    HUMAN = "human"
    PATRON = "patron"


class Speaker(BaseModel):
    """Identifier for who is speaking."""

    kind: SpeakerKind
    name: str
    id: str | None = None


SayingId = NewType("SayingId", str)


class Saying(BaseModel):
    """A saying (message) in a discussion table.

    Sequence is atomically allocated per table and guarantees:
    - Unique (table_id, sequence) tuple
    - Monotonically increasing per table
    - Append-only semantics (no modification/deletion)

    Example:
        >>> from datetime import datetime
        >>> s = Saying(
        ...     id=SayingId("say-001"),
        ...     table_id="table-001",
        ...     sequence=1,
        ...     speaker=Speaker(kind=SpeakerKind.AGENT, name="Test"),
        ...     content="Hello",
        ...     created_at=datetime.now(),
        ... )
        >>> s.sequence
        1
    """

    id: SayingId
    table_id: str
    sequence: int = Field(..., ge=0, description="Monotonically increasing sequence per table")
    speaker: Speaker
    content: str = Field(..., description="Markdown content of the saying")
    pinned: bool = False
    created_at: datetime
