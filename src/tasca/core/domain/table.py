"""
Table domain types.

This module defines the core Table types and related enums.
"""

from datetime import datetime
from enum import Enum
from typing import NewType

from pydantic import BaseModel, Field


class TableStatus(str, Enum):
    """Status of a discussion table."""

    ACTIVE = "active"
    CLOSED = "closed"


TableId = NewType("TableId", str)


class TableCreate(BaseModel):
    """Data required to create a new table."""

    question: str = Field(..., description="The question or topic for discussion")
    context: str | None = Field(None, description="Optional context for the discussion")


class Table(BaseModel):
    """A discussion table where agents collaborate."""

    id: TableId
    question: str
    context: str | None = None
    status: TableStatus = TableStatus.ACTIVE
    created_at: datetime
    updated_at: datetime
