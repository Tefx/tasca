"""
Limits service - core business logic for server-side limits enforcement.

This module provides pure functions for validating various caps:
- history: max sayings per table
- content: max message length
- bytes: max total data per table
- mentions: max mentions per saying

All functions are pure (no I/O) with @pre/@post contracts and doctests.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import deal

# Hypothesis optional - for property-based testing
try:
    from hypothesis import strategies as st
    from hypothesis.strategies import SearchStrategy, register_type_strategy

    _HYPOTHESIS_AVAILABLE = True
except ImportError:
    st = SearchStrategy = register_type_strategy = None  # type: ignore[assignment]
    _HYPOTHESIS_AVAILABLE = False

if TYPE_CHECKING:
    from tasca.config import Settings


class LimitKind(str, Enum):
    """Type of limit that was exceeded."""

    HISTORY = "history"  # Max sayings per table
    CONTENT = "content"  # Max message length
    BYTES = "bytes"  # Max total data per table
    MENTIONS = "mentions"  # Max mentions per saying


@dataclass(frozen=True)
class LimitsConfig:
    """Configuration for server-side limits.

    All limits are positive integers. Use 0 or None to disable a limit.

    Attributes:
        max_sayings_per_table: Maximum number of sayings allowed per table.
        max_content_length: Maximum length of message content in characters.
        max_bytes_per_table: Maximum total bytes stored per table.
        max_mentions_per_saying: Maximum number of @mentions per saying.

    Example:
        >>> config = LimitsConfig(max_sayings_per_table=100, max_content_length=5000)
        >>> config.max_sayings_per_table
        100
        >>> config.max_content_length
        5000
        >>> config.max_bytes_per_table is None
        True
    """

    max_sayings_per_table: int | None = None
    max_content_length: int | None = None
    max_bytes_per_table: int | None = None
    max_mentions_per_saying: int | None = None

    # @invar:allow missing_contract: Dataclass __post_init__ validates invariants via ValueError
    def __post_init__(self) -> None:
        """Validate that all limits are positive."""
        if self.max_sayings_per_table is not None and self.max_sayings_per_table <= 0:
            raise ValueError("max_sayings_per_table must be positive")
        if self.max_content_length is not None and self.max_content_length <= 0:
            raise ValueError("max_content_length must be positive")
        if self.max_bytes_per_table is not None and self.max_bytes_per_table <= 0:
            raise ValueError("max_bytes_per_table must be positive")
        if self.max_mentions_per_saying is not None and self.max_mentions_per_saying < 0:
            raise ValueError("max_mentions_per_saying must be non-negative")


# Register Hypothesis strategy for LimitsConfig (for property-based testing)
# This ensures Hypothesis generates valid configs (positive integers for limits,
# non-negative for mentions) rather than arbitrary integers that would fail __post_init__
# @invar:allow missing_contract: Module-level initialization function, not a core operation
def _register_hypothesis_strategies() -> None:
    """Register custom Hypothesis strategies for this module's types."""
    if not _HYPOTHESIS_AVAILABLE:
        return

    # Strategy for valid LimitsConfig: positive integers for limits, non-negative for mentions
    limits_config_strategy: SearchStrategy[LimitsConfig] = st.builds(  # type: ignore[truthy-function]
        LimitsConfig,
        max_sayings_per_table=st.one_of(st.none(), st.integers(min_value=1)),  # type: ignore[truthy-function]
        max_content_length=st.one_of(st.none(), st.integers(min_value=1)),  # type: ignore[truthy-function]
        max_bytes_per_table=st.one_of(st.none(), st.integers(min_value=1)),  # type: ignore[truthy-function]
        max_mentions_per_saying=st.one_of(st.none(), st.integers(min_value=0)),  # type: ignore[truthy-function]
    )
    register_type_strategy(LimitsConfig, limits_config_strategy)  # type: ignore[truthy-function]


_register_hypothesis_strategies()


@deal.post(lambda result: isinstance(result, LimitsConfig))
def settings_to_limits_config(settings: "Settings") -> LimitsConfig:
    """Convert application Settings to LimitsConfig.

    Args:
        settings: Application settings containing limit values.

    Returns:
        LimitsConfig with values from settings.

    Examples:
        >>> from tasca.config import Settings
        >>> s = Settings(max_sayings_per_table=100, max_content_length=5000)
        >>> config = settings_to_limits_config(s)
        >>> config.max_sayings_per_table
        100
        >>> config.max_content_length
        5000
    """
    return LimitsConfig(
        max_sayings_per_table=settings.max_sayings_per_table,
        max_content_length=settings.max_content_length,
        max_bytes_per_table=settings.max_bytes_per_table,
        max_mentions_per_saying=settings.max_mentions_per_saying,
    )


@dataclass(frozen=True)
class LimitError:
    """Error returned when a limit is exceeded.

    Attributes:
        kind: The type of limit that was exceeded.
        limit: The configured limit value.
        actual: The actual value that exceeded the limit.
        message: Human-readable error message.

    Example:
        >>> error = LimitError(kind=LimitKind.CONTENT, limit=100, actual=150)
        >>> error.kind
        <LimitKind.CONTENT: 'content'>
        >>> error.message
        'content exceeds limit: 150 > 100'
    """

    kind: LimitKind
    limit: int
    actual: int
    message: str = ""

    # @invar:allow missing_contract: Dataclass __post_init__ generates default message
    def __post_init__(self) -> None:
        """Generate message if not provided."""
        if not self.message:
            object.__setattr__(
                self,
                "message",
                f"{self.kind.value} exceeds limit: {self.actual} > {self.limit}",
            )


# =============================================================================
# Core Validation Functions
# =============================================================================

# Mention pattern compiled at module level for reuse
_MENTION_PATTERN = re.compile(r"(?<!\w)@[^\s@]+")


# content may be empty per doctests; max_length must be positive when enabled
@deal.pre(lambda content, max_length: len(content) >= 0 and (max_length is None or max_length > 0))
@deal.post(lambda result: isinstance(result, bool))
def validate_content_length(content: str, max_length: int | None) -> bool:
    """Validate that content length does not exceed the limit.

    Returns True if within limit or limit is disabled (None).
    Returns False if content exceeds the limit.

    Args:
        content: The content string to validate.
        max_length: Maximum allowed length (None = no limit).

    Returns:
        True if content is within limit, False otherwise.

    Examples:
        >>> validate_content_length("Hello", 10)
        True
        >>> validate_content_length("Hello", 5)
        True
        >>> validate_content_length("Hello World", 5)
        False
        >>> validate_content_length("Hello", None)  # No limit
        True
        >>> validate_content_length("", 10)
        True
    """
    if max_length is None:
        return True
    return len(content) <= max_length


# current_count can be 0 per doctests; max_count must be positive when enabled
@deal.pre(
    lambda current_count, max_count: current_count >= 0 and (max_count is None or max_count > 0)
)
@deal.post(lambda result: isinstance(result, bool))
def validate_history_count(current_count: int, max_count: int | None) -> bool:
    """Validate that history count does not exceed the limit.

    Returns True if within limit or limit is disabled (None).
    Returns False if count meets or exceeds the limit (no room for more).

    This is used to check if a new saying can be added.
    A table with max_sayings=100 can have sayings 0-99, so:
    - current_count=99, max_count=100 → True (can add 1 more for total of 100)
    - current_count=100, max_count=100 → False (already at limit)

    Args:
        current_count: Current number of sayings.
        max_count: Maximum allowed count (None = no limit).

    Returns:
        True if there's room for one more saying, False if at limit.

    Examples:
        >>> validate_history_count(50, 100)
        True
        >>> validate_history_count(99, 100)
        True
        >>> validate_history_count(100, 100)
        False
        >>> validate_history_count(150, 100)
        False
        >>> validate_history_count(100, None)  # No limit
        True
        >>> validate_history_count(0, 100)
        True
    """
    if max_count is None:
        return True
    return current_count < max_count


# size_bytes can be 0 per doctests; max_bytes must be positive when enabled
@deal.pre(lambda size_bytes, max_bytes: size_bytes >= 0 and (max_bytes is None or max_bytes > 0))
@deal.post(lambda result: isinstance(result, bool))
def validate_bytes_size(size_bytes: int, max_bytes: int | None) -> bool:
    """Validate that byte size does not exceed the limit.

    Returns True if within limit or limit is disabled (None).
    Returns False if size exceeds the limit.

    Args:
        size_bytes: The size in bytes to validate.
        max_bytes: Maximum allowed bytes (None = no limit).

    Returns:
        True if size is within limit, False otherwise.

    Examples:
        >>> validate_bytes_size(500, 1000)
        True
        >>> validate_bytes_size(1000, 1000)
        True
        >>> validate_bytes_size(1001, 1000)
        False
        >>> validate_bytes_size(500, None)  # No limit
        True
        >>> validate_bytes_size(0, 1000)
        True
    """
    if max_bytes is None:
        return True
    return size_bytes <= max_bytes


# content may be empty per doctests; mentions limit is non-negative when enabled
@deal.pre(
    lambda content, max_mentions: len(content) >= 0 and (max_mentions is None or max_mentions >= 0)
)
@deal.post(lambda result: isinstance(result, bool))
def validate_mentions(content: str, max_mentions: int | None) -> bool:
    """Validate that mention count does not exceed the limit.

    Mentions are detected via the @pattern in content (e.g., @patron-123).
    This is a simple heuristic - it counts @ followed by non-whitespace.

    Returns True if within limit or limit is disabled (None).
    Returns False if mentions exceed the limit.

    Args:
        content: The content string to scan for mentions.
        max_mentions: Maximum allowed mentions (None = no limit).

    Returns:
        True if mentions are within limit, False otherwise.

    Examples:
        >>> validate_mentions("Hello @alice", 5)
        True
        >>> validate_mentions("@alice @bob @charlie", 2)
        False
        >>> validate_mentions("No mentions here", 5)
        True
        >>> validate_mentions("@alice @bob", None)  # No limit
        True
        >>> validate_mentions("Email: test@example.com", 1)  # @ in email
        True
        >>> validate_mentions("", 5)
        True
    """
    if max_mentions is None:
        return True

    # Count mentions: @ followed by non-whitespace characters
    # Pattern excludes emails by requiring @ at start of word boundary
    mentions = _MENTION_PATTERN.findall(content)
    return len(mentions) <= max_mentions


# =============================================================================
# Composite Validation
# =============================================================================


@deal.pre(
    lambda content, current_saying_count, current_bytes, config: (
        len(content) >= 0
        and current_saying_count >= 0
        and current_bytes >= 0
        and config is not None
    )
)
@deal.post(lambda result: result is None or isinstance(result, LimitError))
def check_content_limits(
    content: str,
    current_saying_count: int,
    current_bytes: int,
    config: LimitsConfig,
) -> LimitError | None:
    """Check all content-related limits before appending a saying.

    This checks:
    1. Content length limit
    2. History/saying count limit
    3. Bytes limit (after adding new content)

    Args:
        content: The content to validate.
        current_saying_count: Current number of sayings in the table.
        current_bytes: Current total bytes in the table.
        config: Limits configuration.

    Returns:
        None if all limits pass, or the first LimitError encountered.

    Examples:
        >>> config = LimitsConfig(max_content_length=100, max_sayings_per_table=10)
        >>> check_content_limits("Hello", 5, 1000, config) is None
        True
        >>> error = check_content_limits("x" * 150, 5, 1000, config)
        >>> error.kind
        <LimitKind.CONTENT: 'content'>
        >>> error = check_content_limits("Hi", 10, 1000, config)
        >>> error.kind
        <LimitKind.HISTORY: 'history'>
    """
    # Check content length
    if not validate_content_length(content, config.max_content_length):
        content_limit = config.max_content_length
        assert content_limit is not None
        return LimitError(
            kind=LimitKind.CONTENT,
            limit=content_limit,
            actual=len(content),
        )

    # Check history count (room for one more)
    if not validate_history_count(current_saying_count, config.max_sayings_per_table):
        history_limit = config.max_sayings_per_table
        assert history_limit is not None
        return LimitError(
            kind=LimitKind.HISTORY,
            limit=history_limit,
            actual=current_saying_count,
        )

    # Check bytes limit (would new content exceed it?)
    new_content_bytes = len(content.encode("utf-8"))
    if config.max_bytes_per_table is not None:
        projected_bytes = current_bytes + new_content_bytes
        if not validate_bytes_size(projected_bytes, config.max_bytes_per_table):
            return LimitError(
                kind=LimitKind.BYTES,
                limit=config.max_bytes_per_table,
                actual=projected_bytes,
            )

    # Check mentions
    if not validate_mentions(content, config.max_mentions_per_saying):
        mentions = _MENTION_PATTERN.findall(content)
        mentions_limit = config.max_mentions_per_saying
        assert mentions_limit is not None
        return LimitError(
            kind=LimitKind.MENTIONS,
            limit=mentions_limit,
            actual=len(mentions),
        )

    return None


# =============================================================================
# Utility Functions
# =============================================================================


@deal.post(lambda result: result >= 0)
def compute_content_bytes(content: str) -> int:
    """Compute the byte size of content using UTF-8 encoding.

    Args:
        content: The content string.

    Returns:
        Number of bytes in UTF-8 encoding.

    Examples:
        >>> compute_content_bytes("Hello")
        5
        >>> compute_content_bytes("Hello World")
        11
        >>> compute_content_bytes("")
        0
        >>> compute_content_bytes("日本語")
        9
    """
    return len(content.encode("utf-8"))


@deal.pre(
    lambda current_saying_count, current_bytes, config: (
        current_saying_count >= 0 and current_bytes >= 0 and config is not None
    )
)
@deal.post(lambda result: isinstance(result, dict))
def get_limits_status(
    current_saying_count: int,
    current_bytes: int,
    config: LimitsConfig,
) -> dict[str, dict[str, int | float]]:
    """Get the current status of all limits.

    Returns a dict with limit name -> {current, limit, remaining, percentage}.

    Args:
        current_saying_count: Current number of sayings.
        current_bytes: Current total bytes.
        config: Limits configuration.

    Returns:
        Dict with status for each configured limit.

    Examples:
        >>> config = LimitsConfig(max_sayings_per_table=100, max_content_length=1000)
        >>> status = get_limits_status(50, 5000, config)
        >>> status["history"]["current"]
        50
        >>> status["history"]["remaining"]
        50
        >>> status["history"]["percentage"]
        50.0
    """
    result: dict[str, dict[str, int | float]] = {}

    if config.max_sayings_per_table is not None:
        result["history"] = {
            "current": current_saying_count,
            "limit": config.max_sayings_per_table,
            "remaining": max(0, config.max_sayings_per_table - current_saying_count),
            "percentage": round((current_saying_count / config.max_sayings_per_table) * 100, 2),
        }

    if config.max_bytes_per_table is not None:
        result["bytes"] = {
            "current": current_bytes,
            "limit": config.max_bytes_per_table,
            "remaining": max(0, config.max_bytes_per_table - current_bytes),
            "percentage": round((current_bytes / config.max_bytes_per_table) * 100, 2),
        }

    return result
