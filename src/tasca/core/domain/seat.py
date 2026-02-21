"""
Seat domain types.

This module defines the core Seat types for tracking presence at tables.
"""

from datetime import datetime
from enum import Enum
from typing import NewType

from pydantic import BaseModel


class SeatState(str, Enum):
    """State of a seat at a table."""

    JOINED = "joined"
    LEFT = "left"


# Map spec states (running|idle|done) to internal states
SPEC_STATE_TO_INTERNAL: dict[str, SeatState] = {
    "running": SeatState.JOINED,
    "idle": SeatState.JOINED,  # Idle still counts as joined/present
    "done": SeatState.LEFT,
}

# Map internal states to spec states
INTERNAL_STATE_TO_SPEC: dict[SeatState, str] = {
    SeatState.JOINED: "running",  # Active participation
    SeatState.LEFT: "done",  # Completed
}


SeatId = NewType("SeatId", str)


class Seat(BaseModel):
    """A seat representing an agent's presence at a table."""

    id: SeatId
    table_id: str
    patron_id: str
    state: SeatState
    last_heartbeat: datetime
    joined_at: datetime
