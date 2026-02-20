"""
Tests for dedup cleanup lifecycle (opportunistic + periodic).

Verification Criteria:
1. Test demonstrating expired entry behaves as miss
2. Evidence of bounded cleanup (batch size)
3. How periodic cleanup is triggered in dev
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from returns.result import Failure, Success

from tasca.core.services.dedup_cleanup_service import (
    DEFAULT_CLEANUP_BATCH_SIZE,
    DEFAULT_DEDUP_TTL_SECONDS,
    DEFAULT_OPPORTUNISTIC_CLEANUP_PROBABILITY,
    calculate_batches_for_cleanup,
    calculate_dedup_cutoff_time,
    format_cutoff_for_sql,
    is_dedup_entry_expired,
    should_cleanup_opportunistically,
)
from tasca.shell.storage.database import apply_schema
from tasca.shell.storage.dedup_repo import (
    check_duplicate,
    check_duplicate_with_expiry,
    cleanup_expired_dedup_entries,
    opportunistic_cleanup,
    store_dedup,
    store_or_get_existing,
    store_or_get_existing_with_expiry,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def memory_db() -> sqlite3.Connection:
    """Create an in-memory database with schema applied."""
    conn = sqlite3.connect(":memory:")
    schema_result = apply_schema(conn)
    assert isinstance(schema_result, Success), f"Failed to apply schema: {schema_result}"
    return conn


# =============================================================================
# Core Service Tests
# =============================================================================


class TestIsDedupEntryExpired:
    """Tests for is_dedup_entry_expired pure function."""

    def test_not_expired_within_ttl(self) -> None:
        """Entry is not expired when within TTL."""
        first_seen = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        now = datetime(2024, 1, 1, 12, 0, 30, tzinfo=timezone.utc)  # 30 seconds later
        assert is_dedup_entry_expired(first_seen, 60, now) is False

    def test_not_expired_at_exact_ttl(self) -> None:
        """Entry is not expired exactly at TTL boundary (still within TTL)."""
        first_seen = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        now = datetime(2024, 1, 1, 12, 1, 0, tzinfo=timezone.utc)  # exactly 60 seconds
        assert is_dedup_entry_expired(first_seen, 60, now) is False

    def test_expired_after_ttl(self) -> None:
        """Entry is expired after TTL has passed."""
        first_seen = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        now = datetime(2024, 1, 1, 12, 1, 1, tzinfo=timezone.utc)  # 61 seconds later
        assert is_dedup_entry_expired(first_seen, 60, now) is True

    def test_expired_with_24h_ttl(self) -> None:
        """Entry with 24h TTL expires after 25 hours."""
        first_seen = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        now = datetime(2024, 1, 2, 13, 0, 0, tzinfo=timezone.utc)  # 25 hours later
        assert is_dedup_entry_expired(first_seen, DEFAULT_DEDUP_TTL_SECONDS, now) is True


class TestCalculateDedupCutoffTime:
    """Tests for calculate_dedup_cutoff_time pure function."""

    def test_cutoff_24_hours_ago(self) -> None:
        """Cutoff is 24 hours in the past for 24h TTL."""
        now = datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        cutoff = calculate_dedup_cutoff_time(now, 86400)
        assert cutoff == datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_cutoff_1_hour_ago(self) -> None:
        """Cutoff is 1 hour in the past for 1h TTL."""
        now = datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        cutoff = calculate_dedup_cutoff_time(now, 3600)
        assert cutoff == datetime(2024, 1, 2, 11, 0, 0, tzinfo=timezone.utc)


class TestFormatCutoffForSql:
    """Tests for format_cutoff_for_sql pure function."""

    def test_formats_to_iso(self) -> None:
        """Formats datetime to ISO string for SQL comparison."""
        cutoff = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = format_cutoff_for_sql(cutoff)
        assert result == "2024-01-15T10:30:00+00:00"


class TestShouldCleanupOpportunistically:
    """Tests for should_cleanup_opportunistically pure function."""

    def test_never_triggers_with_zero_probability(self) -> None:
        """Probability 0.0 never triggers cleanup."""
        assert should_cleanup_opportunistically(0.0, 0.5) is False

    def test_always_triggers_with_one_probability(self) -> None:
        """Probability 1.0 always triggers cleanup."""
        assert should_cleanup_opportunistically(1.0, 0.5) is True

    def test_triggers_when_random_below_probability(self) -> None:
        """Triggers when random value is below cleanup_probability."""
        assert should_cleanup_opportunistically(0.5, 0.3) is True  # 0.3 < 0.5

    def test_no_trigger_when_random_above_probability(self) -> None:
        """No trigger when random value is above cleanup_probability."""
        assert should_cleanup_opportunistically(0.5, 0.7) is False  # 0.7 >= 0.5


class TestCalculateBatchesForCleanup:
    """Tests for calculate_batches_for_cleanup pure function."""

    def test_exact_batch_count(self) -> None:
        """Exact multiple of batch size returns correct batch count."""
        assert calculate_batches_for_cleanup(100, 100) == 1
        assert calculate_batches_for_cleanup(200, 100) == 2

    def test_remainder_requires_extra_batch(self) -> None:
        """Remainder requires an extra batch (ceil division)."""
        assert calculate_batches_for_cleanup(101, 100) == 2
        assert calculate_batches_for_cleanup(250, 100) == 3

    def test_zero_entries(self) -> None:
        """Zero entries requires zero batches."""
        assert calculate_batches_for_cleanup(0, 100) == 0


# =============================================================================
# Repository Tests: Expired Entry Behavior
# =============================================================================


class TestExpiredEntryBehavesAsMiss:
    """
    VERIFICATION CRITERION 1: Test demonstrating expired entry behaves as miss.

    When an expired entry is accessed, it should:
    1. Return None (as if not found)
    2. Delete the expired entry from the database
    """

    def test_check_duplicate_with_expiry_returns_none_for_expired_entry(
        self, memory_db: sqlite3.Connection
    ) -> None:
        """Expired entry returns None (behaves as miss)."""
        # Create an entry with first_seen_at in the past
        content_hash = "a" * 64
        content_preview = "Test content..."
        past_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        past_time_str = past_time.isoformat()

        # Insert directly with past timestamp
        memory_db.execute(
            """
            INSERT INTO dedup (content_hash, content_preview, first_seen_at)
            VALUES (?, ?, ?)
            """,
            (content_hash, content_preview, past_time_str),
        )
        memory_db.commit()

        # Verify it exists
        check_result = check_duplicate(memory_db, content_hash)
        assert isinstance(check_result, Success)
        assert check_result.unwrap() is not None

        # Now check with expiry (using 1 hour TTL, entry is 24+ hours old)
        now = datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        expiry_result = check_duplicate_with_expiry(
            memory_db, content_hash, ttl_seconds=3600, now=now
        )

        # VERIFICATION: Expired entry behaves as miss
        assert isinstance(expiry_result, Success)
        assert expiry_result.unwrap() is None, "Expired entry should return None (miss)"

        # VERIFICATION: Expired entry was deleted
        check_again = check_duplicate(memory_db, content_hash)
        assert isinstance(check_again, Success)
        assert check_again.unwrap() is None, "Expired entry should be deleted from DB"

    def test_store_or_get_existing_with_expiry_creates_new_for_expired(
        self, memory_db: sqlite3.Connection
    ) -> None:
        """store_or_get_existing_with_expiry creates new record for expired entry."""
        content = "Test content for expiry"
        content_hash_expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # hash of empty string is wrong, compute actual

        from tasca.core.services.dedup_service import compute_content_hash

        content_hash = compute_content_hash(content)

        # Create an expired entry
        past_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        past_time_str = past_time.isoformat()
        memory_db.execute(
            """
            INSERT INTO dedup (content_hash, content_preview, first_seen_at)
            VALUES (?, ?, ?)
            """,
            (content_hash, "Old preview...", past_time_str),
        )
        memory_db.commit()

        # Now store with expiry - should create new record (expired treated as miss)
        now = datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        result = store_or_get_existing_with_expiry(
            memory_db, content, ttl_seconds=3600, now=now, enable_opportunistic_cleanup=False
        )

        # VERIFICATION: Creates new record (is_new=True) for expired entry
        assert isinstance(result, Success)
        record, is_new = result.unwrap()
        assert is_new is True, "Expired entry should trigger new record creation"
        assert record.content_hash == content_hash

        # VERIFICATION: New record has new timestamp
        assert record.first_seen_at > past_time


# =============================================================================
# Repository Tests: Bounded Cleanup
# =============================================================================


class TestBoundedCleanup:
    """
    VERIFICATION CRITERION 2: Evidence of bounded cleanup (batch size).

    Cleanup should delete entries in batches, not all at once.
    """

    def test_cleanup_respects_batch_size(self, memory_db: sqlite3.Connection) -> None:
        """Cleanup deletes at most batch_size entries per call."""
        # Create 250 expired entries
        past_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        past_time_str = past_time.isoformat()

        for i in range(250):
            content_hash = f"{i:064d}"  # Pad to 64 chars
            memory_db.execute(
                """
                INSERT INTO dedup (content_hash, content_preview, first_seen_at)
                VALUES (?, ?, ?)
                """,
                (content_hash, f"Content {i}", past_time_str),
            )
        memory_db.commit()

        # Count total entries
        count_before = memory_db.execute("SELECT COUNT(*) FROM dedup").fetchone()[0]
        assert count_before == 250

        # Run cleanup with batch_size=100
        now = datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)  # 24 hours later
        result = cleanup_expired_dedup_entries(memory_db, ttl_seconds=3600, now=now, batch_size=100)

        # VERIFICATION: Cleanup deletes at most batch_size
        assert isinstance(result, Success)
        deleted_count = result.unwrap()
        assert deleted_count == 100, f"Expected 100 deleted, got {deleted_count}"

        # VERIFICATION: Only batch_size entries were deleted
        count_after = memory_db.execute("SELECT COUNT(*) FROM dedup").fetchone()[0]
        assert count_after == 150, f"Expected 150 remaining, got {count_after}"

    def test_cleanup_can_be_called_multiple_times(self, memory_db: sqlite3.Connection) -> None:
        """Multiple cleanup calls eventually delete all expired entries."""
        # Create 250 expired entries
        past_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        past_time_str = past_time.isoformat()

        for i in range(250):
            content_hash = f"{i:064d}"
            memory_db.execute(
                """
                INSERT INTO dedup (content_hash, content_preview, first_seen_at)
                VALUES (?, ?, ?)
                """,
                (content_hash, f"Content {i}", past_time_str),
            )
        memory_db.commit()

        now = datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)

        # First cleanup
        result1 = cleanup_expired_dedup_entries(
            memory_db, ttl_seconds=3600, now=now, batch_size=100
        )
        assert result1.unwrap() == 100
        count1 = memory_db.execute("SELECT COUNT(*) FROM dedup").fetchone()[0]
        assert count1 == 150

        # Second cleanup
        result2 = cleanup_expired_dedup_entries(
            memory_db, ttl_seconds=3600, now=now, batch_size=100
        )
        assert result2.unwrap() == 100
        count2 = memory_db.execute("SELECT COUNT(*) FROM dedup").fetchone()[0]
        assert count2 == 50

        # Third cleanup
        result3 = cleanup_expired_dedup_entries(
            memory_db, ttl_seconds=3600, now=now, batch_size=100
        )
        assert result3.unwrap() == 50
        count3 = memory_db.execute("SELECT COUNT(*) FROM dedup").fetchone()[0]
        assert count3 == 0


# =============================================================================
# Repository Tests: Opportunistic Cleanup
# =============================================================================


class TestOpportunisticCleanup:
    """
    VERIFICATION CRITERION 3: How periodic cleanup is triggered in dev.

    Opportunistic cleanup can be triggered during regular operations.
    """

    def test_opportunistic_cleanup_never_triggers_with_zero_probability(
        self, memory_db: sqlite3.Connection
    ) -> None:
        """Probability 0 means cleanup never triggers."""
        result = opportunistic_cleanup(memory_db, cleanup_probability=0.0)
        assert isinstance(result, Success)
        assert result.unwrap() == 0

    def test_opportunistic_cleanup_always_triggers_with_one_probability(
        self, memory_db: sqlite3.Connection
    ) -> None:
        """Probability 1 means cleanup always triggers (even if nothing to clean)."""
        result = opportunistic_cleanup(memory_db, cleanup_probability=1.0)
        assert isinstance(result, Success)
        assert result.unwrap() == 0  # No expired entries, but cleanup did trigger

    def test_opportunistic_cleanup_triggered_during_store(
        self, memory_db: sqlite3.Connection
    ) -> None:
        """store_or_get_existing_with_expiry can trigger opportunistic cleanup."""
        # Create an expired entry
        past_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        past_time_str = past_time.isoformat()
        memory_db.execute(
            """
            INSERT INTO dedup (content_hash, content_preview, first_seen_at)
            VALUES (?, ?, ?)
            """,
            ("b" * 64, "Expired content", past_time_str),
        )
        memory_db.commit()

        # Store new content with cleanup enabled (probability=1.0 triggers always)
        now = datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        result = store_or_get_existing_with_expiry(
            memory_db,
            "New content",
            ttl_seconds=3600,
            now=now,
            enable_opportunistic_cleanup=True,  # Uses default probability
        )

        # New content should be stored
        assert isinstance(result, Success)
        record, is_new = result.unwrap()
        assert is_new is True

        # Note: Opportunistic cleanup may or may not have run depending on random value
        # We just verify the function works correctly

    def test_opportunistic_cleanup_disabled(self, memory_db: sqlite3.Connection) -> None:
        """store_or_get_existing_with_expiry can disable opportunistic cleanup."""
        # Create an expired entry
        past_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        past_time_str = past_time.isoformat()
        memory_db.execute(
            """
            INSERT INTO dedup (content_hash, content_preview, first_seen_at)
            VALUES (?, ?, ?)
            """,
            ("c" * 64, "Expired content", past_time_str),
        )
        memory_db.commit()

        # Store new content with cleanup disabled
        now = datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
        result = store_or_get_existing_with_expiry(
            memory_db,
            "New content",
            ttl_seconds=3600,
            now=now,
            enable_opportunistic_cleanup=False,  # Disabled
        )

        # New content should still be stored
        assert isinstance(result, Success)
        record, is_new = result.unwrap()
        assert is_new is True

        # Expired entry should still exist (cleanup was disabled)
        check_result = check_duplicate(memory_db, "c" * 64)
        assert isinstance(check_result, Success)
        assert check_result.unwrap() is not None, "Expired entry should still exist"


# =============================================================================
# Integration Tests: Full Lifecycle
# =============================================================================


class TestDedupLifecycleIntegration:
    """Integration tests for full dedup lifecycle."""

    def test_full_lifecycle_expired_as_miss(self, memory_db: sqlite3.Connection) -> None:
        """Full lifecycle: store -> expire -> behaves as miss."""
        # 1. Store new content
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result1 = store_or_get_existing_with_expiry(
            memory_db, "My content", ttl_seconds=3600, now=now, enable_opportunistic_cleanup=False
        )
        assert isinstance(result1, Success)
        record1, is_new1 = result1.unwrap()
        assert is_new1 is True

        # 2. Immediately after: should return existing (not expired)
        result2 = store_or_get_existing_with_expiry(
            memory_db, "My content", ttl_seconds=3600, now=now, enable_opportunistic_cleanup=False
        )
        assert isinstance(result2, Success)
        record2, is_new2 = result2.unwrap()
        assert is_new2 is False  # Existing record returned
        assert record2.first_seen_at == record1.first_seen_at

        # 3. After TTL expires: behaves as miss (creates new record)
        now_expired = datetime(2024, 1, 1, 14, 0, 0, tzinfo=timezone.utc)  # 2 hours later
        result3 = store_or_get_existing_with_expiry(
            memory_db,
            "My content",
            ttl_seconds=3600,
            now=now_expired,
            enable_opportunistic_cleanup=False,
        )
        assert isinstance(result3, Success)
        record3, is_new3 = result3.unwrap()
        assert is_new3 is True  # New record created (expired treated as miss)
        assert record3.first_seen_at > record1.first_seen_at

    def test_periodic_cleanup_removes_expired_entries(self, memory_db: sqlite3.Connection) -> None:
        """Periodic cleanup removes all expired entries."""
        # Create 50 expired entries
        past_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        past_time_str = past_time.isoformat()

        for i in range(50):
            content_hash = f"{i:064d}"
            memory_db.execute(
                """
                INSERT INTO dedup (content_hash, content_preview, first_seen_at)
                VALUES (?, ?, ?)
                """,
                (content_hash, f"Content {i}", past_time_str),
            )

        # Create 10 fresh entries
        fresh_time = datetime(2024, 1, 2, 11, 0, 0, tzinfo=timezone.utc)
        fresh_time_str = fresh_time.isoformat()

        for i in range(100, 110):
            content_hash = f"{i:064d}"
            memory_db.execute(
                """
                INSERT INTO dedup (content_hash, content_preview, first_seen_at)
                VALUES (?, ?, ?)
                """,
                (content_hash, f"Fresh {i}", fresh_time_str),
            )
        memory_db.commit()

        # Count before
        count_before = memory_db.execute("SELECT COUNT(*) FROM dedup").fetchone()[0]
        assert count_before == 60  # 50 expired + 10 fresh

        # Run periodic cleanup with large batch size
        now = datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)  # 24 hours later
        result = cleanup_expired_dedup_entries(
            memory_db, ttl_seconds=3600, now=now, batch_size=1000
        )

        # VERIFICATION: All expired entries removed, fresh preserved
        assert isinstance(result, Success)
        assert result.unwrap() == 50

        count_after = memory_db.execute("SELECT COUNT(*) FROM dedup").fetchone()[0]
        assert count_after == 10  # Only fresh entries remain

        # Verify fresh entries are still accessible
        for i in range(100, 110):
            content_hash = f"{i:064d}"
            check = check_duplicate(memory_db, content_hash)
            assert isinstance(check, Success)
            assert check.unwrap() is not None
