"""
Patron domain types.

This module defines the core Patron types for agent identity.
"""

from datetime import datetime
from typing import Any, NewType

from pydantic import BaseModel, Field


PatronId = NewType("PatronId", str)


class PatronCreate(BaseModel):
    """Data required to register a new patron."""

    display_name: str = Field(..., description="Display name for the patron")
    kind: str = Field(default="agent", description="Type of patron (agent/human)")
    alias: str | None = Field(default=None, description="Optional short alias")
    meta: dict[str, Any] | None = Field(default=None, description="Optional metadata")


class Patron(BaseModel):
    """A patron (agent or human) that can participate in discussions."""

    id: PatronId
    name: str  # Internal field name, exposes as display_name in API
    kind: str = "agent"
    alias: str | None = None
    meta: dict[str, Any] | None = None
    created_at: datetime
