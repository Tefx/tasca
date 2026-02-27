"""
Structured logging utilities for shell layer.

This module provides structured logging helpers for observability.
All logs are JSON-formatted for easy parsing by log aggregators.

Usage:
    logger = get_logger(__name__)
    log_event(logger, "table_created", table_id="abc123", speaker="patron:xyz")
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any


# @invar:allow shell_result: Logging utilities - side-effect helpers, no business logic
def get_logger(name: str) -> logging.Logger:
    """Get a logger with structured logging support.

    Args:
        name: Logger name (typically __name__).

    Returns:
        Configured logger instance.
    """
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    event: str,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Log a structured event with JSON formatting.

    Args:
        logger: Logger instance.
        event: Event name (e.g., "table_created", "dedup_hit").
        level: Log level (default INFO).
        **fields: Additional structured fields to include.

    Example:
        >>> logger = get_logger(__name__)
        >>> log_event(logger, "table_created", table_id="abc", speaker="patron:xyz")
    """
    log_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    logger.log(level, json.dumps(log_data))


def log_dedup_hit(
    logger: logging.Logger,
    operation: str,
    resource_key: str,
    dedup_id: str,
) -> None:
    """Log a dedup cache hit.

    This is a convenience function for the common dedup hit pattern.

    Args:
        logger: Logger instance.
        operation: Operation name (e.g., "table_create", "table_say").
        resource_key: Resource scope key.
        dedup_id: Client-provided idempotency key.

    Example:
        >>> logger = get_logger(__name__)
        >>> log_dedup_hit(logger, "table_say", "saying:table-123:patron-456", "dedup-789")
    """
    log_event(
        logger,
        "dedup_hit",
        operation=operation,
        scope_key=resource_key,
        dedup_id=dedup_id,
    )


def log_table_create(
    logger: logging.Logger,
    table_id: str,
    speaker: str,
) -> None:
    """Log table creation event.

    Args:
        logger: Logger instance.
        table_id: Created table ID.
        speaker: Speaker identifier (e.g., "patron:xyz" or "human").

    Example:
        >>> logger = get_logger(__name__)
        >>> log_table_create(logger, "table-123", "patron:agent-1")
    """
    log_event(logger, "table_created", table_id=table_id, speaker=speaker)


def log_table_update(
    logger: logging.Logger,
    table_id: str,
    version: int,
    speaker: str,
) -> None:
    """Log table update event.

    Args:
        logger: Logger instance.
        table_id: Updated table ID.
        version: New version number.
        speaker: Speaker identifier.

    Example:
        >>> logger = get_logger(__name__)
        >>> log_table_update(logger, "table-123", 2, "patron:agent-1")
    """
    log_event(logger, "table_updated", table_id=table_id, version=version, speaker=speaker)


def log_table_delete(
    logger: logging.Logger,
    table_id: str,
    speaker: str,
) -> None:
    """Log table deletion event.

    Args:
        logger: Logger instance.
        table_id: Deleted table ID.
        speaker: Speaker identifier.

    Example:
        >>> logger = get_logger(__name__)
        >>> log_table_delete(logger, "table-123", "patron:agent-1")
    """
    log_event(logger, "table_deleted", table_id=table_id, speaker=speaker)


def log_batch_table_delete(
    logger: logging.Logger,
    table_ids: list[str],
    speaker: str,
) -> None:
    """Log batch table deletion event.

    Args:
        logger: Logger instance.
        table_ids: List of deleted table IDs.
        speaker: Speaker identifier.

    Example:
        >>> logger = get_logger(__name__)
        >>> log_batch_table_delete(logger, ["t1", "t2"], "rest:admin")
    """
    log_event(
        logger,
        "tables_batch_deleted",
        table_ids=table_ids,
        count=len(table_ids),
        speaker=speaker,
    )


def log_say(
    logger: logging.Logger,
    table_id: str,
    sequence: int,
    speaker_kind: str,
    speaker_name: str,
    patron_id: str | None,
) -> None:
    """Log saying append event.

    Args:
        logger: Logger instance.
        table_id: Table ID.
        sequence: Sequence number of the saying.
        speaker_kind: "agent" or "human".
        speaker_name: Display name of speaker.
        patron_id: Patron ID if agent, None if human.

    Example:
        >>> logger = get_logger(__name__)
        >>> log_say(logger, "table-123", 1, "agent", "Claude", "patron-456")
    """
    speaker = f"patron:{patron_id}" if patron_id else "human"
    log_event(
        logger,
        "saying_appended",
        table_id=table_id,
        sequence=sequence,
        speaker_kind=speaker_kind,
        speaker_name=speaker_name,
        speaker=speaker,
    )


def log_wait_timeout(
    logger: logging.Logger,
    table_id: str,
    since_sequence: int,
) -> None:
    """Log wait timeout event.

    Args:
        logger: Logger instance.
        table_id: Table ID.
        since_sequence: Sequence number client was waiting from.

    Example:
        >>> logger = get_logger(__name__)
        >>> log_wait_timeout(logger, "table-123", 5)
    """
    log_event(
        logger,
        "wait_timeout",
        table_id=table_id,
        since_sequence=since_sequence,
    )


def log_wait_returned(
    logger: logging.Logger,
    table_id: str,
    since_sequence: int,
    count: int,
) -> None:
    """Log wait returned with new sayings.

    Args:
        logger: Logger instance.
        table_id: Table ID.
        since_sequence: Sequence number client was waiting from.
        count: Number of new sayings returned.

    Example:
        >>> logger = get_logger(__name__)
        >>> log_wait_returned(logger, "table-123", 5, 3)
    """
    log_event(
        logger,
        "wait_returned",
        table_id=table_id,
        since_sequence=since_sequence,
        count=count,
    )
