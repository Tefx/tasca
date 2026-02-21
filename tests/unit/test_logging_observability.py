"""
Observability tests for structured logging.

Tests use pytest's caplog fixture to capture and verify structured JSON log output.
"""

from __future__ import annotations

import json
import logging

import pytest

from tasca.shell.logging import (
    get_logger,
    log_dedup_hit,
    log_event,
    log_say,
    log_table_create,
    log_table_delete,
    log_table_update,
    log_wait_returned,
    log_wait_timeout,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def captured_logger() -> logging.Logger:
    """Create a logger that can be captured by caplog."""
    logger = get_logger("tasca.test.observability")
    logger.setLevel(logging.DEBUG)
    return logger


# =============================================================================
# Core log_event Tests
# =============================================================================


class TestLogEvent:
    """Tests for the core log_event function."""

    def test_log_event_emits_json_with_event_field(
        self, captured_logger: logging.Logger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """log_event emits JSON with event field as the identifying marker."""
        caplog.set_level(logging.INFO, logger="tasca.test.observability")

        log_event(captured_logger, "test_event", key1="value1", key2="value2")

        assert len(caplog.records) == 1
        record = caplog.records[0]
        log_data = json.loads(record.getMessage())

        # L1: Assert event field matches expected name
        assert log_data["event"] == "test_event"

        # Assert required structured fields are present
        assert "timestamp" in log_data
        assert log_data["key1"] == "value1"
        assert log_data["key2"] == "value2"

    def test_log_event_includes_iso_timestamp(
        self, captured_logger: logging.Logger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """log_event includes ISO-formatted UTC timestamp."""
        caplog.set_level(logging.INFO, logger="tasca.test.observability")

        log_event(captured_logger, "timestamp_test")

        log_data = json.loads(caplog.records[0].getMessage())
        timestamp = log_data["timestamp"]

        # ISO format contains 'T' separator and timezone
        assert "T" in timestamp
        assert timestamp.endswith("+00:00") or "Z" in timestamp or "+" in timestamp

    def test_log_event_supports_different_log_levels(
        self, captured_logger: logging.Logger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """log_event can emit at different log levels."""
        caplog.set_level(logging.DEBUG, logger="tasca.test.observability")

        log_event(captured_logger, "info_event", level=logging.INFO)
        log_event(captured_logger, "warning_event", level=logging.WARNING)
        log_event(captured_logger, "error_event", level=logging.ERROR)

        assert len(caplog.records) == 3
        assert caplog.records[0].levelname == "INFO"
        assert caplog.records[1].levelname == "WARNING"
        assert caplog.records[2].levelname == "ERROR"


# =============================================================================
# Table Lifecycle Log Tests
# =============================================================================


class TestTableLifecycleLogging:
    """Tests for table create/update/delete structured logging."""

    def test_log_table_create_emits_structured_event(
        self, captured_logger: logging.Logger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """log_table_create emits structured JSON with table_id and speaker."""
        caplog.set_level(logging.INFO, logger="tasca.test.observability")

        log_table_create(captured_logger, table_id="table-123", speaker="patron:agent-1")

        assert len(caplog.records) == 1
        log_data = json.loads(caplog.records[0].getMessage())

        # L1: Assert event field matches expected name
        assert log_data["event"] == "table_created"

        # Assert required structured fields are present
        assert log_data["table_id"] == "table-123"
        assert log_data["speaker"] == "patron:agent-1"
        assert "timestamp" in log_data

    def test_log_table_update_emits_structured_event(
        self, captured_logger: logging.Logger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """log_table_update emits structured JSON with table_id, version, and speaker."""
        caplog.set_level(logging.INFO, logger="tasca.test.observability")

        log_table_update(captured_logger, table_id="table-456", version=3, speaker="rest:admin")

        log_data = json.loads(caplog.records[0].getMessage())

        # L1: Assert event field matches expected name
        assert log_data["event"] == "table_updated"

        # Assert required structured fields are present
        assert log_data["table_id"] == "table-456"
        assert log_data["version"] == 3
        assert log_data["speaker"] == "rest:admin"
        assert "timestamp" in log_data

    def test_log_table_delete_emits_structured_event(
        self, captured_logger: logging.Logger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """log_table_delete emits structured JSON with table_id and speaker."""
        caplog.set_level(logging.INFO, logger="tasca.test.observability")

        log_table_delete(captured_logger, table_id="table-789", speaker="human")

        log_data = json.loads(caplog.records[0].getMessage())

        # L1: Assert event field matches expected name
        assert log_data["event"] == "table_deleted"

        # Assert required structured fields are present
        assert log_data["table_id"] == "table-789"
        assert log_data["speaker"] == "human"
        assert "timestamp" in log_data


# =============================================================================
# Saying Log Tests
# =============================================================================


class TestSayingLogging:
    """Tests for saying append structured logging."""

    def test_log_say_agent_emits_structured_event(
        self, captured_logger: logging.Logger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """log_say with agent patron emits structured JSON with agent speaker."""
        caplog.set_level(logging.INFO, logger="tasca.test.observability")

        log_say(
            captured_logger,
            table_id="table-abc",
            sequence=5,
            speaker_kind="agent",
            speaker_name="Claude",
            patron_id="patron-xyz",
        )

        log_data = json.loads(caplog.records[0].getMessage())

        # L1: Assert event field matches expected name
        assert log_data["event"] == "saying_appended"

        # Assert required structured fields are present
        assert log_data["table_id"] == "table-abc"
        assert log_data["sequence"] == 5
        assert log_data["speaker_kind"] == "agent"
        assert log_data["speaker_name"] == "Claude"
        assert log_data["speaker"] == "patron:patron-xyz"
        assert "timestamp" in log_data

    def test_log_say_human_emits_structured_event(
        self, captured_logger: logging.Logger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """log_say with human speaker emits structured JSON with 'human' speaker."""
        caplog.set_level(logging.INFO, logger="tasca.test.observability")

        log_say(
            captured_logger,
            table_id="table-def",
            sequence=2,
            speaker_kind="human",
            speaker_name="Alice",
            patron_id=None,
        )

        log_data = json.loads(caplog.records[0].getMessage())

        assert log_data["event"] == "saying_appended"
        assert log_data["speaker"] == "human"
        assert log_data["speaker_kind"] == "human"
        assert log_data["speaker_name"] == "Alice"


# =============================================================================
# Dedup Log Tests
# =============================================================================


class TestDedupLogging:
    """Tests for dedup cache hit structured logging."""

    def test_log_dedup_hit_emits_structured_event(
        self, captured_logger: logging.Logger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """log_dedup_hit emits structured JSON with dedup context."""
        caplog.set_level(logging.INFO, logger="tasca.test.observability")

        log_dedup_hit(
            captured_logger,
            operation="table_create",
            resource_key="saying:table-123:patron-456",
            dedup_id="dedup-789",
        )

        log_data = json.loads(caplog.records[0].getMessage())

        # L1: Assert event field matches expected name
        assert log_data["event"] == "dedup_hit"

        # Assert required structured fields are present
        assert log_data["operation"] == "table_create"
        assert log_data["scope_key"] == "saying:table-123:patron-456"
        assert log_data["dedup_id"] == "dedup-789"
        assert "timestamp" in log_data


# =============================================================================
# Wait Log Tests
# =============================================================================


class TestWaitLogging:
    """Tests for wait timeout/returned structured logging."""

    def test_log_wait_timeout_emits_structured_event(
        self, captured_logger: logging.Logger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """log_wait_timeout emits structured JSON with timeout context."""
        caplog.set_level(logging.INFO, logger="tasca.test.observability")

        log_wait_timeout(captured_logger, table_id="table-wait", since_sequence=10)

        log_data = json.loads(caplog.records[0].getMessage())

        assert log_data["event"] == "wait_timeout"
        assert log_data["table_id"] == "table-wait"
        assert log_data["since_sequence"] == 10

    def test_log_wait_returned_emits_structured_event(
        self, captured_logger: logging.Logger, caplog: pytest.LogCaptureFixture
    ) -> None:
        """log_wait_returned emits structured JSON with return context."""
        caplog.set_level(logging.INFO, logger="tasca.test.observability")

        log_wait_returned(captured_logger, table_id="table-wait", since_sequence=10, count=3)

        log_data = json.loads(caplog.records[0].getMessage())

        assert log_data["event"] == "wait_returned"
        assert log_data["table_id"] == "table-wait"
        assert log_data["since_sequence"] == 10
        assert log_data["count"] == 3
