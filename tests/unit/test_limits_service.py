"""
Tests for limits_service core logic.

Tests verify that all limit validation functions work correctly:
- validate_content_length: message length limit
- validate_history_count: sayings count limit
- validate_bytes_size: byte size limit
- validate_mentions: mentions count limit
- check_content_limits: composite validation
"""

import deal
import pytest

from tasca.core.services.limits_service import (
    LimitError,
    LimitKind,
    LimitsConfig,
    check_content_limits,
    compute_content_bytes,
    get_limits_status,
    validate_bytes_size,
    validate_content_length,
    validate_history_count,
    validate_mentions,
)


# =============================================================================
# LimitsConfig Tests
# =============================================================================


class TestLimitsConfig:
    """Tests for LimitsConfig dataclass."""

    def test_default_values(self) -> None:
        """All limits default to None (disabled)."""
        config = LimitsConfig()
        assert config.max_sayings_per_table is None
        assert config.max_content_length is None
        assert config.max_bytes_per_table is None
        assert config.max_mentions_per_saying is None

    def test_custom_values(self) -> None:
        """Custom limits are stored correctly."""
        config = LimitsConfig(
            max_sayings_per_table=100,
            max_content_length=5000,
            max_bytes_per_table=1_000_000,
            max_mentions_per_saying=10,
        )
        assert config.max_sayings_per_table == 100
        assert config.max_content_length == 5000
        assert config.max_bytes_per_table == 1_000_000
        assert config.max_mentions_per_saying == 10

    def test_partial_config(self) -> None:
        """Can set only some limits."""
        config = LimitsConfig(max_content_length=1000)
        assert config.max_content_length == 1000
        assert config.max_sayings_per_table is None

    def test_invalid_max_sayings(self) -> None:
        """Zero or negative max_sayings_per_table raises ValueError."""
        with pytest.raises(ValueError, match="max_sayings_per_table must be positive"):
            LimitsConfig(max_sayings_per_table=0)

        with pytest.raises(ValueError, match="max_sayings_per_table must be positive"):
            LimitsConfig(max_sayings_per_table=-1)

    def test_invalid_max_content_length(self) -> None:
        """Zero or negative max_content_length raises ValueError."""
        with pytest.raises(ValueError, match="max_content_length must be positive"):
            LimitsConfig(max_content_length=0)

    def test_invalid_max_bytes(self) -> None:
        """Zero or negative max_bytes_per_table raises ValueError."""
        with pytest.raises(ValueError, match="max_bytes_per_table must be positive"):
            LimitsConfig(max_bytes_per_table=0)

    def test_invalid_max_mentions(self) -> None:
        """Negative max_mentions_per_saying raises ValueError."""
        with pytest.raises(ValueError, match="max_mentions_per_saying must be non-negative"):
            LimitsConfig(max_mentions_per_saying=-1)

    def test_zero_mentions_allowed(self) -> None:
        """Zero max_mentions_per_saying is allowed (disable all mentions)."""
        config = LimitsConfig(max_mentions_per_saying=0)
        assert config.max_mentions_per_saying == 0


# =============================================================================
# LimitError Tests
# =============================================================================


class TestLimitError:
    """Tests for LimitError dataclass."""

    def test_error_creation(self) -> None:
        """LimitError stores kind, limit, and actual."""
        error = LimitError(kind=LimitKind.CONTENT, limit=100, actual=150)
        assert error.kind == LimitKind.CONTENT
        assert error.limit == 100
        assert error.actual == 150

    def test_auto_message_generation(self) -> None:
        """Message is auto-generated if not provided."""
        error = LimitError(kind=LimitKind.HISTORY, limit=50, actual=60)
        assert error.message == "history exceeds limit: 60 > 50"

    def test_custom_message(self) -> None:
        """Custom message is preserved."""
        error = LimitError(
            kind=LimitKind.BYTES,
            limit=1000,
            actual=1500,
            message="Custom error message",
        )
        assert error.message == "Custom error message"

    def test_all_limit_kinds(self) -> None:
        """All limit kinds can create errors."""
        for kind in LimitKind:
            error = LimitError(kind=kind, limit=100, actual=150)
            assert error.kind == kind
            assert kind.value in error.message


# =============================================================================
# validate_content_length Tests
# =============================================================================


class TestValidateContentLength:
    """Tests for validate_content_length function."""

    def test_within_limit(self) -> None:
        """Content within limit returns True."""
        assert validate_content_length("Hello", 10) is True
        assert validate_content_length("Hello", 5) is True  # Exact match

    def test_exceeds_limit(self) -> None:
        """Content exceeding limit returns False."""
        assert validate_content_length("Hello World", 5) is False
        assert validate_content_length("x" * 1000, 100) is False

    def test_no_limit(self) -> None:
        """None limit always returns True."""
        assert validate_content_length("Hello", None) is True
        assert validate_content_length("x" * 1000000, None) is True

    def test_empty_content(self) -> None:
        """Empty content is always valid."""
        assert validate_content_length("", 10) is True
        assert validate_content_length("", 1) is True
        assert validate_content_length("", None) is True

    def test_unicode_content(self) -> None:
        """Unicode characters counted by character, not bytes."""
        assert validate_content_length("日本語", 3) is True
        assert validate_content_length("日本語", 2) is False


# =============================================================================
# validate_history_count Tests
# =============================================================================


class TestValidateHistoryCount:
    """Tests for validate_history_count function."""

    def test_within_limit(self) -> None:
        """Count within limit returns True (room for one more)."""
        assert validate_history_count(50, 100) is True
        assert validate_history_count(99, 100) is True  # Room for 1 more

    def test_at_limit(self) -> None:
        """Count at limit returns False (no room for more)."""
        assert validate_history_count(100, 100) is False
        assert validate_history_count(101, 100) is False

    def test_no_limit(self) -> None:
        """None limit always returns True."""
        assert validate_history_count(1000, None) is True
        assert validate_history_count(1000000, None) is True

    def test_zero_count(self) -> None:
        """Zero count is always valid."""
        assert validate_history_count(0, 100) is True
        assert validate_history_count(0, 1) is True
        assert validate_history_count(0, None) is True


# =============================================================================
# validate_bytes_size Tests
# =============================================================================


class TestValidateBytesSize:
    """Tests for validate_bytes_size function."""

    def test_within_limit(self) -> None:
        """Size within limit returns True."""
        assert validate_bytes_size(500, 1000) is True
        assert validate_bytes_size(1000, 1000) is True  # Exact match

    def test_exceeds_limit(self) -> None:
        """Size exceeding limit returns False."""
        assert validate_bytes_size(1001, 1000) is False
        assert validate_bytes_size(2000, 1000) is False

    def test_no_limit(self) -> None:
        """None limit always returns True."""
        assert validate_bytes_size(1000000, None) is True
        assert validate_bytes_size(1 << 30, None) is True  # 1GB

    def test_zero_size(self) -> None:
        """Zero size is always valid."""
        assert validate_bytes_size(0, 1000) is True
        assert validate_bytes_size(0, 1) is True
        assert validate_bytes_size(0, None) is True


# =============================================================================
# validate_mentions Tests
# =============================================================================


class TestValidateMentions:
    """Tests for validate_mentions function."""

    def test_no_mentions(self) -> None:
        """Content without mentions is always valid."""
        assert validate_mentions("Hello world", 5) is True
        assert validate_mentions("No mentions here", 0) is True

    def test_within_limit(self) -> None:
        """Mentions within limit returns True."""
        assert validate_mentions("Hello @alice", 5) is True
        assert validate_mentions("@alpha @beta @gamma", 3) is True

    def test_exceeds_limit(self) -> None:
        """Mentions exceeding limit returns False."""
        assert validate_mentions("@alpha @beta @gamma", 2) is False
        assert validate_mentions("@alone", 0) is False  # Zero limit

    def test_no_limit(self) -> None:
        """None limit always returns True."""
        assert validate_mentions("@alpha @beta @gamma", None) is True
        assert validate_mentions("spam" * 100, None) is True

    def test_email_not_mention(self) -> None:
        """Email addresses (@ in middle) don't count as mentions."""
        assert validate_mentions("Email: test@example.com", 0) is True
        assert validate_mentions("Contact: user@domain.org", 0) is True

    def test_multiple_mentions(self) -> None:
        """Multiple mentions are counted correctly."""
        content = "@alice @bob @charlie"
        assert validate_mentions(content, 3) is True
        assert validate_mentions(content, 2) is False

    def test_empty_content(self) -> None:
        """Empty content has no mentions."""
        assert validate_mentions("", 0) is True
        assert validate_mentions("", 5) is True


# =============================================================================
# check_content_limits Tests
# =============================================================================


class TestCheckContentLimits:
    """Tests for check_content_limits composite function."""

    def test_all_pass(self) -> None:
        """All limits pass returns None."""
        config = LimitsConfig(
            max_content_length=100,
            max_sayings_per_table=10,
            max_bytes_per_table=10000,
        )
        result = check_content_limits("Hello", 5, 500, config)
        assert result is None

    def test_content_limit_exceeded(self) -> None:
        """Content limit exceeded returns error."""
        config = LimitsConfig(max_content_length=10)
        error = check_content_limits("x" * 20, 5, 500, config)
        assert error is not None
        assert error.kind == LimitKind.CONTENT
        assert error.limit == 10
        assert error.actual == 20

    def test_history_limit_exceeded(self) -> None:
        """History limit exceeded returns error."""
        config = LimitsConfig(max_sayings_per_table=10)
        error = check_content_limits("Hello", 10, 500, config)
        assert error is not None
        assert error.kind == LimitKind.HISTORY
        assert error.limit == 10

    def test_bytes_limit_exceeded(self) -> None:
        """Bytes limit exceeded returns error."""
        config = LimitsConfig(max_bytes_per_table=1000)
        # Current 500 + new content "Hello" (5 bytes) = 505, but let's make it exceed
        error = check_content_limits("x" * 1000, 5, 500, config)  # 500 + 1000 > 1000
        assert error is not None
        assert error.kind == LimitKind.BYTES
        assert error.limit == 1000

    def test_mentions_limit_exceeded(self) -> None:
        """Mentions limit exceeded returns error."""
        config = LimitsConfig(max_mentions_per_saying=2)
        error = check_content_limits("@alice @bob @charlie", 5, 500, config)
        assert error is not None
        assert error.kind == LimitKind.MENTIONS
        assert error.limit == 2
        assert error.actual == 3

    def test_no_limits(self) -> None:
        """No limits configured always passes."""
        config = LimitsConfig()
        result = check_content_limits("x" * 10000, 1000, 1000000, config)
        assert result is None

    def test_first_error_returned(self) -> None:
        """Returns first error in priority order (content first)."""
        config = LimitsConfig(
            max_content_length=5,
            max_sayings_per_table=5,
            max_bytes_per_table=100,
        )
        error = check_content_limits("x" * 10, 10, 500, config)
        assert error is not None
        assert error.kind == LimitKind.CONTENT  # Content checked first

    def test_check_order_content(self) -> None:
        """Content limit checked before history."""
        config = LimitsConfig(
            max_content_length=5,
            max_sayings_per_table=5,
        )
        error = check_content_limits("x" * 10, 10, 500, config)
        assert error is not None
        assert error.kind == LimitKind.CONTENT

    def test_check_order_history(self) -> None:
        """History limit checked after content."""
        config = LimitsConfig(
            max_content_length=100,
            max_sayings_per_table=5,
        )
        error = check_content_limits("short", 10, 500, config)
        assert error is not None
        assert error.kind == LimitKind.HISTORY

    def test_check_content_limits_rejects_negative_saying_count(self) -> None:
        config = LimitsConfig(max_sayings_per_table=10)
        with pytest.raises(deal.PreContractError):
            check_content_limits("hello", -1, 0, config)

    def test_check_content_limits_rejects_negative_bytes(self) -> None:
        config = LimitsConfig(max_sayings_per_table=10)
        with pytest.raises(deal.PreContractError):
            check_content_limits("hello", 0, -1, config)


# =============================================================================
# compute_content_bytes Tests
# =============================================================================


class TestComputeContentBytes:
    """Tests for compute_content_bytes function."""

    def test_ascii_content(self) -> None:
        """ASCII content: 1 byte per character."""
        assert compute_content_bytes("Hello") == 5
        assert compute_content_bytes("Hello World") == 11

    def test_empty_content(self) -> None:
        """Empty content is 0 bytes."""
        assert compute_content_bytes("") == 0

    def test_unicode_content(self) -> None:
        """Unicode content: UTF-8 encoding."""
        # Japanese characters are 3 bytes each in UTF-8
        assert compute_content_bytes("日本語") == 9  # 3 chars * 3 bytes
        # Emoji can be 4 bytes
        assert compute_content_bytes("😀") == 4

    def test_mixed_content(self) -> None:
        """Mixed ASCII and Unicode."""
        # "Hi " = 3 ASCII bytes + "日本" = 6 bytes (2 chars * 3)
        assert compute_content_bytes("Hi 日本") == 9


# =============================================================================
# get_limits_status Tests
# =============================================================================


class TestGetLimitsStatus:
    """Tests for get_limits_status function."""

    def test_history_status(self) -> None:
        """History status is correct."""
        config = LimitsConfig(max_sayings_per_table=100)
        status = get_limits_status(50, 5000, config)
        assert "history" in status
        assert status["history"]["current"] == 50
        assert status["history"]["limit"] == 100
        assert status["history"]["remaining"] == 50
        assert status["history"]["percentage"] == 50.0

    def test_bytes_status(self) -> None:
        """Bytes status is correct."""
        config = LimitsConfig(max_bytes_per_table=10000)
        status = get_limits_status(20, 2500, config)
        assert "bytes" in status
        assert status["bytes"]["current"] == 2500
        assert status["bytes"]["limit"] == 10000
        assert status["bytes"]["remaining"] == 7500
        assert status["bytes"]["percentage"] == 25.0

    def test_no_limits(self) -> None:
        """No limits configured returns empty dict."""
        config = LimitsConfig()
        status = get_limits_status(100, 10000, config)
        assert status == {}

    def test_at_limit(self) -> None:
        """At limit shows 0 remaining."""
        config = LimitsConfig(max_sayings_per_table=100)
        status = get_limits_status(100, 0, config)
        assert status["history"]["remaining"] == 0
        assert status["history"]["percentage"] == 100.0

    def test_over_limit(self) -> None:
        """Over limit shows 0 remaining (clamped)."""
        config = LimitsConfig(max_sayings_per_table=100)
        status = get_limits_status(150, 0, config)
        assert status["history"]["remaining"] == 0
        assert status["history"]["percentage"] == 150.0

    def test_combined_status(self) -> None:
        """Multiple limits shown together."""
        config = LimitsConfig(max_sayings_per_table=100, max_bytes_per_table=10000)
        status = get_limits_status(50, 5000, config)
        assert "history" in status
        assert "bytes" in status

    def test_get_limits_status_rejects_negative_saying_count(self) -> None:
        config = LimitsConfig(max_sayings_per_table=10)
        with pytest.raises(deal.PreContractError):
            get_limits_status(-1, 0, config)

    def test_get_limits_status_rejects_negative_bytes(self) -> None:
        config = LimitsConfig(max_sayings_per_table=10)
        with pytest.raises(deal.PreContractError):
            get_limits_status(0, -1, config)


# =============================================================================
# Error Response Payload Examples
# =============================================================================


class TestErrorResponsePayloads:
    """Examples of error/warning response payloads."""

    def test_content_error_payload(self) -> None:
        """Example: Content limit exceeded error payload."""
        config = LimitsConfig(max_content_length=100)
        error = check_content_limits("x" * 150, 10, 5000, config)
        assert error is not None

        # Example error response payload
        payload = {
            "error": {
                "code": "LIMIT_EXCEEDED",
                "kind": error.kind.value,
                "message": error.message,
                "details": {
                    "limit": error.limit,
                    "actual": error.actual,
                },
            }
        }
        assert payload["error"]["code"] == "LIMIT_EXCEEDED"
        assert payload["error"]["kind"] == "content"
        assert payload["error"]["details"]["limit"] == 100
        assert payload["error"]["details"]["actual"] == 150

    def test_history_error_payload(self) -> None:
        """Example: History limit exceeded error payload."""
        config = LimitsConfig(max_sayings_per_table=10)
        error = check_content_limits("Hello", 10, 5000, config)
        assert error is not None

        payload = {
            "error": {
                "code": "LIMIT_EXCEEDED",
                "kind": error.kind.value,
                "message": error.message,
                "details": {
                    "limit": error.limit,
                    "actual": error.actual,
                },
            }
        }
        assert payload["error"]["kind"] == "history"

    def test_warning_payload_near_limit(self) -> None:
        """Example: Warning when approaching limit (80%+)."""
        config = LimitsConfig(max_sayings_per_table=100)
        status = get_limits_status(85, 0, config)

        # Example warning payload (not an error, but advisory)
        payload = {
            "warning": {
                "code": "APPROACHING_LIMIT",
                "kind": "history",
                "message": f"History at {status['history']['percentage']}% of limit",
                "details": {
                    "current": status["history"]["current"],
                    "limit": status["history"]["limit"],
                    "remaining": status["history"]["remaining"],
                    "percentage": status["history"]["percentage"],
                },
            }
        }
        assert payload["warning"]["code"] == "APPROACHING_LIMIT"
        assert payload["warning"]["details"]["percentage"] == 85.0

    def test_multi_limit_status_payload(self) -> None:
        """Example: Full limits status response payload."""
        config = LimitsConfig(
            max_sayings_per_table=100,
            max_bytes_per_table=1_000_000,
        )
        status = get_limits_status(75, 750000, config)

        payload = {
            "limits": {
                "history": {
                    "used": status["history"]["current"],
                    "max": status["history"]["limit"],
                    "remaining": status["history"]["remaining"],
                    "percentage": status["history"]["percentage"],
                },
                "bytes": {
                    "used": status["bytes"]["current"],
                    "max": status["bytes"]["limit"],
                    "remaining": status["bytes"]["remaining"],
                    "percentage": status["bytes"]["percentage"],
                },
            }
        }
        assert payload["limits"]["history"]["percentage"] == 75.0
        assert payload["limits"]["bytes"]["percentage"] == 75.0
