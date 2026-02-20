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


SeatId = NewType("SeatId", str)


class Seat(BaseModel):
    """A seat representing an agent's presence at a table."""

    id: SeatId
    table_id: str
    patron_id: str
    state: SeatState
    last_heartbeat: datetime
    joined_at: datetime
