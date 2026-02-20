"""
Core contracts - Protocol definitions and type aliases.

This module defines the abstract interfaces (protocols) that shell
implementations must satisfy. No concrete implementations here.
"""

from typing import Protocol


# Placeholder protocols - will be expanded as services are implemented


class TableRepository(Protocol):
    """Protocol for table storage operations."""

    pass


class SayingRepository(Protocol):
    """Protocol for saying storage operations."""

    pass


class SeatRepository(Protocol):
    """Protocol for seat storage operations."""

    pass


class PatronRepository(Protocol):
    """Protocol for patron storage operations."""

    pass
