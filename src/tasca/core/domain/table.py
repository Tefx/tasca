"""
Table domain types.

This module defines the core Table types and related enums.
"""

from datetime import datetime
from enum import Enum
from typing import NewType

from pydantic import BaseModel, Field


class TableStatus(str, Enum):
    """Status of a discussion table.

    State transitions:
    - OPEN → PAUSED (pause)
    - OPEN → CLOSED (close)
    - PAUSED → OPEN (resume)
    - PAUSED → CLOSED (close)
    - CLOSED → (terminal, no transitions)

    Operation permissions:
    - OPEN: can_say=True, can_join=True
    - PAUSED: can_say=True, can_join=False (soft pause for say)
    - CLOSED: can_say=False, can_join=False (terminal)
    """

    OPEN = "open"
    PAUSED = "paused"
    CLOSED = "closed"


TableId = NewType("TableId", str)

# Version type for optimistic concurrency
Version = NewType("Version", int)


class TableCreate(BaseModel):
    """Data required to create a new table."""

    question: str = Field(..., description="The question or topic for discussion")
    context: str | None = Field(None, description="Optional context for the discussion")


class TableUpdate(BaseModel):
    """Data for updating a table (full replace).

    Replace-only semantics: all fields are required and replace existing values.
    This is NOT a partial patch - caller MUST provide all updatable fields.

    For context field:
    - Provide string value to set/update context
    - Provide null to explicitly clear context
    - Omitting context is NOT allowed (prevents accidental clearing)
    """

    question: str = Field(..., description="The question or topic for discussion")
    context: str | None = Field(..., description="Context for the discussion (null to clear)")
    status: TableStatus = Field(..., description="The table status")


class Table(BaseModel):
    """A discussion table where agents collaborate.

    Attributes:
        id: Unique identifier for the table.
        question: The question or topic for discussion.
        context: Optional context for the discussion.
        status: Current status of the table.
        version: Version number for optimistic concurrency.
            Starts at 1 and increments on each update.
        created_at: Timestamp when the table was created.
        updated_at: Timestamp when the table was last updated.
    """

    id: TableId
    question: str
    context: str | None = None
    status: TableStatus = TableStatus.OPEN
    version: Version = Field(default=Version(1), description="Version for optimistic concurrency")
    created_at: datetime
    updated_at: datetime
    creator_patron_id: str | None = None
