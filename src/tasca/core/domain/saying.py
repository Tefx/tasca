"""
Saying domain types.

This module defines the core Saying types for messages in discussions.
Atomic sequence allocation ensures each saying has a unique (table_id, sequence) tuple.

Speaker Semantics:
    - patron_id IS None → Human speaker (not managed by the system)
    - patron_id IS NOT None → AI Patron (managed agent in the system)
"""

from datetime import datetime
from enum import Enum
from typing import NewType

import deal
from pydantic import BaseModel, Field

from tasca.core.domain.patron import PatronId


class SpeakerKind(str, Enum):
    """Kind of speaker in a discussion.

    The kind is derived from the patron_id:
    - HUMAN: patron_id is None (human user)
    - AGENT/PATRON: patron_id is set (AI patron)
    """

    AGENT = "agent"
    HUMAN = "human"
    PATRON = "patron"


class Speaker(BaseModel):
    """Identifier for who is speaking.

    patron_id semantics:
        - None: Human speaker (external user, not managed by system)
        - PatronId: AI Patron (registered agent managed by the system)

    Example:
        >>> human = Speaker(kind=SpeakerKind.HUMAN, name="Alice")
        >>> human.patron_id is None
        True
        >>> human.is_human()
        True
    """

    kind: SpeakerKind
    name: str
    patron_id: PatronId | None = None

    @deal.post(lambda result: isinstance(result, bool))
    def is_human(self) -> bool:
        """Check if this speaker is a human (not an AI patron).

        >>> Speaker(kind=SpeakerKind.HUMAN, name="Alice").is_human()
        True
        >>> Speaker(kind=SpeakerKind.AGENT, name="Bot", patron_id=PatronId("p-001")).is_human()
        False
        """
        return self.patron_id is None

    @deal.post(lambda result: isinstance(result, bool))
    def is_patron(self) -> bool:
        """Check if this speaker is an AI patron.

        >>> Speaker(kind=SpeakerKind.AGENT, name="Bot", patron_id=PatronId("p-001")).is_patron()
        True
        >>> Speaker(kind=SpeakerKind.HUMAN, name="Alice").is_patron()
        False
        """
        return self.patron_id is not None


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
        ...     speaker=Speaker(kind=SpeakerKind.AGENT, name="Test", patron_id=PatronId("p-001")),
        ...     content="Hello",
        ...     created_at=datetime.now(),
        ... )
        >>> s.sequence
        1
        >>> s.speaker.is_patron()
        True
    """

    id: SayingId
    table_id: str
    sequence: int = Field(..., ge=0, description="Monotonically increasing sequence per table")
    speaker: Speaker
    content: str = Field(..., description="Markdown content of the saying")
    pinned: bool = False
    created_at: datetime


# =============================================================================
# Speaker Factory Functions
# =============================================================================


@deal.pre(lambda name: len(name) > 0)
@deal.post(lambda result: result.is_human() is True)
@deal.post(lambda result: result.patron_id is None)
def human_speaker(name: str) -> Speaker:
    """Create a Speaker for a human (non-patron) user.

    Human speakers have patron_id = None, distinguishing them from AI patrons.

    Args:
        name: Display name for the human speaker (non-empty).

    Returns:
        Speaker with kind=HUMAN and patron_id=None.

    Example:
        >>> speaker = human_speaker("Alice")
        >>> speaker.name
        'Alice'
        >>> speaker.kind
        <SpeakerKind.HUMAN: 'human'>
        >>> speaker.is_human()
        True
        >>> speaker.patron_id is None
        True
    """
    return Speaker(kind=SpeakerKind.HUMAN, name=name, patron_id=None)


@deal.pre(lambda name, patron_id: len(name) > 0)
@deal.pre(lambda name, patron_id: len(patron_id) > 0)
@deal.post(lambda result: result.is_patron() is True)
@deal.post(lambda result: result.patron_id is not None)
def patron_speaker(name: str, patron_id: PatronId) -> Speaker:
    """Create a Speaker for an AI patron (managed agent).

    Patron speakers have their patron_id set, linking them to the Patron registry.

    Args:
        name: Display name for the patron (non-empty).
        patron_id: ID of the registered patron.

    Returns:
        Speaker with kind=AGENT and the given patron_id.

    Example:
        >>> from tasca.core.domain.patron import PatronId
        >>> speaker = patron_speaker("HelperBot", PatronId("p-123"))
        >>> speaker.name
        'HelperBot'
        >>> speaker.kind
        <SpeakerKind.AGENT: 'agent'>
        >>> speaker.is_patron()
        True
        >>> speaker.patron_id
        'p-123'
    """
    return Speaker(kind=SpeakerKind.AGENT, name=name, patron_id=patron_id)
