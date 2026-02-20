"""
Saying domain types.

This module defines the core Saying types for messages in discussions.
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
    """A saying (message) in a discussion table."""

    id: SayingId
    table_id: str
    speaker: Speaker
    content: str = Field(..., description="Markdown content of the saying")
    pinned: bool = False
    created_at: datetime
