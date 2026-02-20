"""
Patron domain types.

This module defines the core Patron types for agent identity.
"""

from datetime import datetime
from typing import NewType

from pydantic import BaseModel, Field


PatronId = NewType("PatronId", str)


class PatronCreate(BaseModel):
    """Data required to register a new patron."""

    name: str = Field(..., description="Name or identifier for the patron")
    kind: str = Field(default="agent", description="Type of patron (agent/human)")


class Patron(BaseModel):
    """A patron (agent or human) that can participate in discussions."""

    id: PatronId
    name: str
    kind: str = "agent"
    created_at: datetime
