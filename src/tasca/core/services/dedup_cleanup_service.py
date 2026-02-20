"""
Dedup cleanup service - core business logic for dedup entry lifecycle.

This module provides pure functions for dedup entry TTL and expiry logic.
All functions are pure (no I/O) with @deal.pre/@deal.post contracts.

Cleanup Strategies:
    - Opportunistic: Triggered during access (e.g., store_or_get_existing)
    - Periodic: Scheduled cleanup batch operation
    - Expired entries behave as misses (ignored or deleted)
"""

from datetime import datetime, timedelta

import deal


# =============================================================================
# TTL Constants
# =============================================================================

# Default TTL in seconds (24 hours)
DEFAULT_DEDUP_TTL_SECONDS = 86400

# Default batch size for periodic cleanup
DEFAULT_CLEANUP_BATCH_SIZE = 100

# Default opportunistic cleanup probability (10% chance)
DEFAULT_OPPORTUNISTIC_CLEANUP_PROBABILITY = 0.1

# Reasonable TTL range (1 second to 365 days)
MIN_TTL_SECONDS = 1
MAX_TTL_SECONDS = 31536000  # 365 days


# =============================================================================
# TTL Calculations (Pure Logic)
# =============================================================================


@deal.pre(
    lambda first_seen_at, ttl_seconds, now: (
        first_seen_at is not None
        and MIN_TTL_SECONDS <= ttl_seconds <= MAX_TTL_SECONDS
        and now is not None
    )
)
@deal.post(lambda result: isinstance(result, bool))
def is_dedup_entry_expired(first_seen_at: datetime, ttl_seconds: int, now: datetime) -> bool:
    """Check if a dedup entry has expired based on TTL.

    An entry is expired when the time since first_seen_at exceeds TTL.
    Expired entries should be treated as misses (not found).

    Args:
        first_seen_at: When the entry was first created.
        ttl_seconds: Time-to-live in seconds. Must be positive.
        now: Current timestamp.

    Returns:
        True if the entry has expired.
        False if the entry is still valid.

    Doctests:
        >>> from datetime import datetime
        >>> first_seen = datetime(2024, 1, 1, 12, 0, 0)
        >>> # TTL of 60 seconds, 30 seconds passed - not expired
        >>> is_dedup_entry_expired(first_seen, 60, datetime(2024, 1, 1, 12, 0, 30))
        False
        >>> # TTL of 60 seconds, exactly 60 seconds - not expired (still within TTL)
        >>> is_dedup_entry_expired(first_seen, 60, datetime(2024, 1, 1, 12, 1, 0))
        False
        >>> # TTL of 60 seconds, 61 seconds passed - expired
        >>> is_dedup_entry_expired(first_seen, 60, datetime(2024, 1, 1, 12, 1, 1))
        True
        >>> # Default TTL (24 hours), 25 hours passed - expired
        >>> is_dedup_entry_expired(first_seen, DEFAULT_DEDUP_TTL_SECONDS, datetime(2024, 1, 2, 13, 0, 0))
        True
    """
    expiry_time = first_seen_at + timedelta(seconds=ttl_seconds)
    return now > expiry_time


@deal.pre(
    lambda now, ttl_seconds: now is not None and MIN_TTL_SECONDS <= ttl_seconds <= MAX_TTL_SECONDS
)
@deal.post(lambda result: result is not None)
def calculate_dedup_cutoff_time(now: datetime, ttl_seconds: int) -> datetime:
    """Calculate the cutoff time for dedup entry expiry.

    Entries with first_seen_at earlier than the cutoff are expired.

    Args:
        now: Current timestamp.
        ttl_seconds: Time-to-live in seconds. Must be positive.

    Returns:
        The cutoff datetime. Entries with first_seen_at < cutoff are expired.

    Doctests:
        >>> from datetime import datetime
        >>> now = datetime(2024, 1, 2, 12, 0, 0)
        >>> # 24 hour TTL
        >>> cutoff = calculate_dedup_cutoff_time(now, 86400)
        >>> cutoff == datetime(2024, 1, 1, 12, 0, 0)
        True
        >>> # 1 hour TTL
        >>> cutoff = calculate_dedup_cutoff_time(now, 3600)
        >>> cutoff == datetime(2024, 1, 2, 11, 0, 0)
        True
    """
    return now - timedelta(seconds=ttl_seconds)


@deal.pre(lambda cutoff: cutoff is not None)
@deal.post(lambda result: isinstance(result, str))
def format_cutoff_for_sql(cutoff: datetime) -> str:
    """Format cutoff datetime for SQL comparison.

    Args:
        cutoff: The cutoff datetime.

    Returns:
        ISO format string suitable for SQL comparison.

    Doctests:
        >>> from datetime import datetime
        >>> cutoff = datetime(2024, 1, 15, 10, 30, 0)
        >>> format_cutoff_for_sql(cutoff)
        '2024-01-15T10:30:00'
    """
    return cutoff.isoformat()


@deal.pre(
    lambda cleanup_probability, random_value: (
        0.0 <= cleanup_probability <= 1.0 and 0.0 <= random_value <= 1.0
    )
)
@deal.post(lambda result: isinstance(result, bool))
def should_cleanup_opportunistically(cleanup_probability: float, random_value: float) -> bool:
    """Determine if opportunistic cleanup should run.

    Uses an injected random value (0.0-1.0) to decide whether to trigger cleanup.
    This prevents cleanup from running on every operation while still
    providing regular cleanup opportunities.

    IMPORTANT: random_value should be injected by the shell layer (e.g., random.random()).
    This function is pure and deterministic.

    Args:
        cleanup_probability: Probability of triggering cleanup (0.0 to 1.0).
            - 0.0: Never cleanup opportunistically
            - 1.0: Always cleanup opportunistically
            - 0.1: 10% chance (default)
        random_value: Random value between 0.0 and 1.0 (injected by shell).

    Returns:
        True if cleanup should be triggered.

    Doctests:
        >>> should_cleanup_opportunistically(0.0, 0.5)  # Never triggers
        False
        >>> should_cleanup_opportunistically(1.0, 0.5)  # Always triggers
        True
        >>> should_cleanup_opportunistically(0.5, 0.3)  # 0.3 < 0.5 -> triggers
        True
        >>> should_cleanup_opportunistically(0.5, 0.7)  # 0.7 >= 0.5 -> no trigger
        False
    """
    if cleanup_probability == 0.0:
        return False
    if cleanup_probability == 1.0:
        return True
    return random_value < cleanup_probability


@deal.pre(lambda total_entries, batch_size: total_entries >= 0 and batch_size > 0)
@deal.post(lambda result: isinstance(result, int) and result >= 0)
def calculate_batches_for_cleanup(total_entries: int, batch_size: int) -> int:
    """Calculate the number of batches needed for cleanup.

    Args:
        total_entries: Total number of entries to clean up.
        batch_size: Maximum entries per batch.

    Returns:
        Number of batches needed (ceil division).

    Doctests:
        >>> calculate_batches_for_cleanup(100, 100)
        1
        >>> calculate_batches_for_cleanup(101, 100)
        2
        >>> calculate_batches_for_cleanup(0, 100)
        0
        >>> calculate_batches_for_cleanup(250, 100)
        3
    """
    if total_entries == 0:
        return 0
    return (total_entries + batch_size - 1) // batch_size
