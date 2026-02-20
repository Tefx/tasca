"""
Tests for dedup_service core logic and dedup_repo return_existing semantics.

Core tests verify hash computation and preview truncation.
Repository tests verify:
- Store new content
- Return existing on duplicate (return_existing)
- Isolation between different content hashes
"""

import os
import sqlite3
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from returns.result import Failure, Success

from tasca.core.services.dedup_service import (
    compute_content_hash,
    compute_hash_and_preview,
    truncate_preview,
)
from tasca.shell.storage.database import apply_schema
from tasca.shell.storage.dedup_repo import (
    check_duplicate,
    store_dedup,
    store_or_get_existing,
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


class TestComputeContentHash:
    """Tests for compute_content_hash pure function."""

    def test_empty_string(self) -> None:
        """Empty string has a known SHA-256 hash."""
        hash_result = compute_content_hash("")
        assert hash_result == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_deterministic(self) -> None:
        """Same input always produces same hash."""
        hash1 = compute_content_hash("Hello, world!")
        hash2 = compute_content_hash("Hello, world!")
        assert hash1 == hash2

    def test_different_inputs(self) -> None:
        """Different inputs produce different hashes."""
        hash1 = compute_content_hash("Hello")
        hash2 = compute_content_hash("World")
        assert hash1 != hash2

    def test_length(self) -> None:
        """SHA-256 hash is always 64 characters (hex)."""
        assert len(compute_content_hash("")) == 64
        assert len(compute_content_hash("short")) == 64
        assert len(compute_content_hash("a" * 10000)) == 64

    def test_unicode_content(self) -> None:
        """Unicode content is handled correctly."""
        hash_result = compute_content_hash("Hello 世界 🌍")
        assert len(hash_result) == 64
        assert hash_result == compute_content_hash("Hello 世界 🌍")


class TestTruncatePreview:
    """Tests for truncate_preview pure function."""

    def test_short_content(self) -> None:
        """Short content is not truncated."""
        assert truncate_preview("Short") == "Short"

    def test_exact_length(self) -> None:
        """Content exactly at max_length is not truncated."""
        content = "A" * 200
        assert truncate_preview(content) == content
        assert len(truncate_preview(content)) == 200

    def test_long_content(self) -> None:
        """Long content is truncated with ellipsis."""
        content = "A" * 250
        result = truncate_preview(content)
        assert len(result) == 203  # 200 + "..."
        assert result.endswith("...")
        assert result[:-3] == "A" * 200

    def test_custom_max_length(self) -> None:
        """Custom max_length is respected."""
        content = "B" * 100
        result = truncate_preview(content, max_length=50)
        assert len(result) == 53  # 50 + "..."
        assert result.endswith("...")

    def test_empty_content(self) -> None:
        """Empty string returns empty string."""
        assert truncate_preview("") == ""

    def test_preserves_content_within_limit(self) -> None:
        """Content within limit is preserved exactly."""
        content = "This is a test message"
        assert truncate_preview(content) == content


class TestComputeHashAndPreview:
    """Tests for compute_hash_and_preview convenience function."""

    def test_returns_both(self) -> None:
        """Returns both hash and preview."""
        content = "Test content for hashing"
        content_hash, content_preview = compute_hash_and_preview(content)

        assert len(content_hash) == 64
        assert content_preview == content  # Short content, no truncation

    def test_matches_individual_functions(self) -> None:
        """Result matches calling individual functions."""
        content = "A" * 300

        hash1, preview1 = compute_hash_and_preview(content)
        hash2 = compute_content_hash(content)
        preview2 = truncate_preview(content)

        assert hash1 == hash2
        assert preview1 == preview2

    def test_custom_preview_length(self) -> None:
        """Custom preview length is passed through."""
        content = "X" * 100
        content_hash, content_preview = compute_hash_and_preview(content, preview_max_length=50)

        assert len(content_hash) == 64
        assert len(content_preview) == 53


# =============================================================================
# Repository Tests
# =============================================================================


class TestCheckDuplicate:
    """Tests for check_duplicate operation."""

    def test_not_found(self, memory_db: sqlite3.Connection) -> None:
        """Returns None for non-existent hash."""
        result = check_duplicate(memory_db, "nonexistent" + "0" * 54)
        assert isinstance(result, Success)
        assert result.unwrap() is None

    def test_found_after_store(self, memory_db: sqlite3.Connection) -> None:
        """Returns record after storing."""
        content_hash = "a" * 64
        content_preview = "Test preview..."

        # Store first
        store_result = store_dedup(memory_db, content_hash, content_preview)
        assert isinstance(store_result, Success)

        # Check should find it
        check_result = check_duplicate(memory_db, content_hash)
        assert isinstance(check_result, Success)
        record = check_result.unwrap()
        assert record is not None
        assert record.content_hash == content_hash
        assert record.content_preview == content_preview


class TestStoreDedup:
    """Tests for store_dedup operation."""

    def test_store_new(self, memory_db: sqlite3.Connection) -> None:
        """Can store a new dedup record."""
        content_hash = "b" * 64
        content_preview = "New content preview..."

        result = store_dedup(memory_db, content_hash, content_preview)
        assert isinstance(result, Success)
        record = result.unwrap()
        assert record.content_hash == content_hash
        assert record.content_preview == content_preview
        assert record.first_seen_at is not None

    def test_store_duplicate_returns_existing(self, memory_db: sqlite3.Connection) -> None:
        """Storing duplicate returns existing record."""
        content_hash = "c" * 64
        content_preview = "First preview..."

        # Store first
        first_result = store_dedup(memory_db, content_hash, content_preview)
        assert isinstance(first_result, Success)
        first_record = first_result.unwrap()
        first_seen_at = first_record.first_seen_at

        # Store again with same hash
        second_result = store_dedup(memory_db, content_hash, "Different preview...")
        assert isinstance(second_result, Success)
        second_record = second_result.unwrap()

        # Should return the original record
        assert second_record.content_hash == content_hash
        assert second_record.content_preview == content_preview
        assert second_record.first_seen_at == first_seen_at


class TestStoreOrGetExisting:
    """Tests for store_or_get_existing with return_existing semantics."""

    def test_new_content(self, memory_db: sqlite3.Connection) -> None:
        """New content creates new record with is_new=True."""
        result = store_or_get_existing(memory_db, "First unique content")
        assert isinstance(result, Success)
        record, is_new = result.unwrap()

        assert is_new is True
        assert record.content_hash == compute_content_hash("First unique content")
        assert record.content_preview == "First unique content"
        assert record.first_seen_at is not None

    def test_duplicate_returns_existing(self, memory_db: sqlite3.Connection) -> None:
        """Duplicate content returns existing record with is_new=False."""
        content = "This is duplicate content"

        # First call - creates new
        result1 = store_or_get_existing(memory_db, content)
        assert isinstance(result1, Success)
        record1, is_new1 = result1.unwrap()
        assert is_new1 is True
        first_seen_at = record1.first_seen_at

        # Second call - returns existing
        result2 = store_or_get_existing(memory_db, content)
        assert isinstance(result2, Success)
        record2, is_new2 = result2.unwrap()
        assert is_new2 is False
        assert record2.content_hash == record1.content_hash
        assert record2.first_seen_at == first_seen_at

    def test_different_content_creates_new(self, memory_db: sqlite3.Connection) -> None:
        """Different content creates new record."""
        # Store first content
        result1 = store_or_get_existing(memory_db, "Content A")
        assert isinstance(result1, Success)
        record1, is_new1 = result1.unwrap()
        assert is_new1 is True

        # Store second different content
        result2 = store_or_get_existing(memory_db, "Content B")
        assert isinstance(result2, Success)
        record2, is_new2 = result2.unwrap()
        assert is_new2 is True
        assert record2.content_hash != record1.content_hash

    def test_preview_truncation(self, memory_db: sqlite3.Connection) -> None:
        """Long content is truncated in preview."""
        long_content = "X" * 500
        result = store_or_get_existing(memory_db, long_content)
        assert isinstance(result, Success)
        record, _ = result.unwrap()

        assert len(record.content_preview) == 203  # 200 + "..."
        assert record.content_preview.endswith("...")

    def test_empty_content(self, memory_db: sqlite3.Connection) -> None:
        """Empty content is handled correctly."""
        result = store_or_get_existing(memory_db, "")
        assert isinstance(result, Success)
        record, is_new = result.unwrap()

        assert is_new is True
        # Empty string has known hash
        assert record.content_hash == compute_content_hash("")
        assert record.content_preview == ""


class TestReturnExistingSemantics:
    """
    Tests specifically verifying return_existing behavior.

    These tests demonstrate the key requirement:
    "When a duplicate is detected, return the existing record instead of creating a new one."
    """

    def test_before_after_response_json(self, memory_db: sqlite3.Connection) -> None:
        """
        Verify before/after response for a dedup hit.

        This test shows the JSON structure that would be returned:
        - Before: New record created (is_new=true)
        - After: Existing record returned (is_new=false)
        """
        import json

        content = "Test content for dedup"

        # BEFORE: First store - creates new record
        result_before = store_or_get_existing(memory_db, content)
        assert isinstance(result_before, Success)
        record_before, is_new_before = result_before.unwrap()

        before_json = json.dumps(
            {
                "content_hash": record_before.content_hash,
                "content_preview": record_before.content_preview,
                "first_seen_at": record_before.first_seen_at.isoformat(),
                "is_new": is_new_before,
            }
        )

        # AFTER: Second store - returns existing record
        result_after = store_or_get_existing(memory_db, content)
        assert isinstance(result_after, Success)
        record_after, is_new_after = result_after.unwrap()

        after_json = json.dumps(
            {
                "content_hash": record_after.content_hash,
                "content_preview": record_after.content_preview,
                "first_seen_at": record_after.first_seen_at.isoformat(),
                "is_new": is_new_after,
            }
        )

        # Verify: Same hash, same preview, same timestamp
        assert record_after.content_hash == record_before.content_hash
        assert record_after.content_preview == record_before.content_preview
        assert record_after.first_seen_at == record_before.first_seen_at

        # Verify: is_new flags differ
        assert is_new_before is True
        assert is_new_after is False

        # Print for evidence
        print("\n=== BEFORE (new content) ===")
        print(before_json)
        print("\n=== AFTER (duplicate detected, existing returned) ===")
        print(after_json)

    def test_multiple_duplicates_all_return_same(self, memory_db: sqlite3.Connection) -> None:
        """Multiple calls with same content all return the original record."""
        content = "Repeated content"

        # First call
        result1 = store_or_get_existing(memory_db, content)
        record1, _ = result1.unwrap()
        original_time = record1.first_seen_at

        # Multiple subsequent calls
        for _ in range(10):
            result = store_or_get_existing(memory_db, content)
            record, is_new = result.unwrap()
            assert is_new is False
            assert record.first_seen_at == original_time
            assert record.content_hash == record1.content_hash


class TestConcurrencyDedup:
    """
    Concurrency tests for dedup operations.

    Verify that under concurrent access:
    1. No duplicate records are created
    2. return_existing semantics work correctly
    """

    def test_concurrent_same_content(self) -> None:
        """
        Concurrent stores of same content should all return same record.
        Only one should have is_new=True (or all get existing via IntegrityError).
        """
        content = "Concurrent test content"
        content_hash = compute_content_hash(content)

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            # Initialize database
            init_conn = sqlite3.connect(db_path)
            init_conn.execute("PRAGMA journal_mode=WAL")
            apply_schema(init_conn)
            init_conn.close()

            results: list = []
            lock = threading.Lock()

            def store_concurrently() -> tuple[bool, str]:
                """Store content and return (is_new, hash)."""
                thread_conn = sqlite3.connect(db_path)
                thread_conn.execute("PRAGMA busy_timeout=5000")
                try:
                    result = store_or_get_existing(thread_conn, content)
                    if isinstance(result, Success):
                        record, is_new = result.unwrap()
                        return is_new, record.content_hash
                    return False, "error"
                finally:
                    thread_conn.close()

            # Run concurrent stores
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(store_concurrently) for _ in range(10)]
                results = [f.result() for f in as_completed(futures)]

            # All should have the same hash
            hashes = [r[1] for r in results]
            assert all(h == content_hash for h in hashes), f"Hashes mismatch: {hashes}"

            # Check that only one or zero threads created (IntegrityError recovery)
            is_new_count = sum(1 for r in results if r[0])
            assert is_new_count <= 1, f"Expected at most 1 new record, got {is_new_count}"

            # Verify only one record in database
            verify_conn = sqlite3.connect(db_path)
            check_result = check_duplicate(verify_conn, content_hash)
            verify_conn.close()
            assert isinstance(check_result, Success)
            assert check_result.unwrap() is not None

        finally:
            os.unlink(db_path)

    def test_concurrent_different_content(self) -> None:
        """Concurrent stores of different content should all create new records."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            # Initialize database
            init_conn = sqlite3.connect(db_path)
            init_conn.execute("PRAGMA journal_mode=WAL")
            apply_schema(init_conn)
            init_conn.close()

            def store_unique(i: int) -> bool:
                """Store unique content."""
                thread_conn = sqlite3.connect(db_path)
                thread_conn.execute("PRAGMA busy_timeout=5000")
                try:
                    result = store_or_get_existing(thread_conn, f"Unique content {i}")
                    if isinstance(result, Success):
                        _, is_new = result.unwrap()
                        return is_new
                    return False
                finally:
                    thread_conn.close()

            # Run concurrent stores with different content
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(store_unique, i) for i in range(10)]
                results = [f.result() for f in as_completed(futures)]

            # All should be new
            assert all(results), f"All unique content should create new records: {results}"

        finally:
            os.unlink(db_path)
