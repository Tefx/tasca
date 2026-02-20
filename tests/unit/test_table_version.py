"""
Unit tests for table optimistic concurrency and replace-only update.

Tests for:
- Version validation functions (core)
- Replace-only update preparation (core)
- VersionConflictError behavior (shell)
- Replace-only update in repository (shell)
"""

import sqlite3
from datetime import datetime

import pytest
from returns.result import Failure, Success

from tasca.core.domain.table import Table, TableId, TableStatus, TableUpdate, Version
from tasca.core.services.table_service import (
    VersionMismatchError,
    check_version_or_raise,
    increment_version,
    prepare_table_update,
    prepare_versioned_update,
    validate_version_match,
)
from tasca.shell.storage.table_repo import (
    TableNotFoundError,
    VersionConflictError,
    create_table,
    create_tables_table,
    get_table,
    update_table,
)


# =============================================================================
# Core: Version Validation Tests
# =============================================================================


class TestValidateVersionMatch:
    """Tests for validate_version_match function."""

    def test_matching_versions_return_true(self) -> None:
        """Matching versions return True."""
        assert validate_version_match(Version(1), Version(1)) is True
        assert validate_version_match(Version(5), Version(5)) is True
        assert validate_version_match(Version(100), Version(100)) is True

    def test_mismatched_versions_return_false(self) -> None:
        """Mismatched versions return False."""
        assert validate_version_match(Version(1), Version(2)) is False
        assert validate_version_match(Version(2), Version(1)) is False
        assert validate_version_match(Version(5), Version(10)) is False


class TestIncrementVersion:
    """Tests for increment_version function."""

    def test_increment_adds_one(self) -> None:
        """Version increments by 1."""
        assert increment_version(Version(1)) == 2
        assert increment_version(Version(5)) == 6
        assert increment_version(Version(100)) == 101


class TestCheckVersionOrRaise:
    """Tests for check_version_or_raise function."""

    def test_matching_versions_no_raise(self) -> None:
        """Matching versions don't raise."""
        # These should not raise
        check_version_or_raise(Version(1), Version(1))
        check_version_or_raise(Version(10), Version(10))

    def test_mismatched_versions_raise(self) -> None:
        """Mismatched versions raise VersionMismatchError."""
        with pytest.raises(VersionMismatchError) as exc_info:
            check_version_or_raise(Version(1), Version(2))

        assert exc_info.value.current_version == Version(1)
        assert exc_info.value.expected_version == Version(2)

    def test_version_mismatch_error_message(self) -> None:
        """VersionMismatchError has descriptive message."""
        with pytest.raises(VersionMismatchError) as exc_info:
            check_version_or_raise(Version(5), Version(3))

        assert "expected 3" in str(exc_info.value)
        assert "current is 5" in str(exc_info.value)


# =============================================================================
# Core: Replace-Only Update Tests
# =============================================================================


class TestPrepareTableUpdate:
    """Tests for prepare_table_update function (replace-only semantics)."""

    @pytest.fixture
    def sample_table(self) -> Table:
        """Create a sample table for testing."""
        return Table(
            id=TableId("table-123"),
            question="Original question",
            context="Original context",
            status=TableStatus.OPEN,
            version=Version(1),
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            updated_at=datetime(2024, 1, 1, 12, 0, 0),
        )

    def test_replace_only_updates_all_fields(self, sample_table: Table) -> None:
        """Replace-only: update replaces all provided fields."""
        update = TableUpdate(
            question="New question",
            context="New context",
            status=TableStatus.PAUSED,
        )
        now = datetime(2024, 1, 2, 12, 0, 0)

        result = prepare_table_update(sample_table, update, now)

        assert result.question == "New question"
        assert result.context == "New context"
        assert result.status == TableStatus.PAUSED

    def test_replace_only_preserves_id(self, sample_table: Table) -> None:
        """Replace-only: id is preserved."""
        update = TableUpdate(
            question="Updated",
            context=None,
            status=TableStatus.OPEN,
        )

        result = prepare_table_update(sample_table, update, datetime.now())

        assert result.id == sample_table.id

    def test_replace_only_preserves_created_at(self, sample_table: Table) -> None:
        """Replace-only: created_at is preserved."""
        update = TableUpdate(
            question="Updated",
            context=None,
            status=TableStatus.OPEN,
        )

        result = prepare_table_update(sample_table, update, datetime.now())

        assert result.created_at == sample_table.created_at

    def test_replace_only_updates_version(self, sample_table: Table) -> None:
        """Replace-only: version is incremented."""
        update = TableUpdate(
            question="Updated",
            context=None,
            status=TableStatus.OPEN,
        )

        result = prepare_table_update(sample_table, update, datetime.now())

        assert result.version == Version(2)

    def test_replace_only_updates_timestamp(self, sample_table: Table) -> None:
        """Replace-only: updated_at is set to now."""
        update = TableUpdate(
            question="Updated",
            context=None,
            status=TableStatus.OPEN,
        )
        now = datetime(2024, 6, 15, 10, 30, 0)

        result = prepare_table_update(sample_table, update, now)

        assert result.updated_at == now

    def test_replace_only_context_can_be_cleared(self, sample_table: Table) -> None:
        """Replace-only: context can be explicitly set to None."""
        update = TableUpdate(
            question="Updated",
            context=None,  # Explicitly clearing context
            status=TableStatus.OPEN,
        )

        result = prepare_table_update(sample_table, update, datetime.now())

        assert result.context is None

    def test_context_not_preserved_from_original(self, sample_table: Table) -> None:
        """Replace-only: context is NOT preserved if not in update.

        This demonstrates replace-only semantics: the update fully replaces,
        does not merge. If you want the original context, include it in update.
        """
        update = TableUpdate(
            question="Updated",
            context="New context",  # Must explicitly include
            status=TableStatus.OPEN,
        )

        result = prepare_table_update(sample_table, update, datetime.now())

        assert result.context == "New context"


class TestPrepareVersionedUpdate:
    """Tests for prepare_versioned_update (optimistic concurrency + replace-only)."""

    @pytest.fixture
    def sample_table(self) -> Table:
        """Create a sample table for testing."""
        return Table(
            id=TableId("table-456"),
            question="Question",
            context="Context",
            status=TableStatus.OPEN,
            version=Version(3),
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            updated_at=datetime(2024, 1, 1, 12, 0, 0),
        )

    def test_correct_version_succeeds(self, sample_table: Table) -> None:
        """Update with correct expected_version succeeds."""
        update = TableUpdate(
            question="Updated question",
            context="Updated context",
            status=TableStatus.PAUSED,
        )

        result = prepare_versioned_update(sample_table, update, Version(3), datetime.now())

        assert result.question == "Updated question"
        assert result.version == Version(4)

    def test_wrong_version_raises(self, sample_table: Table) -> None:
        """Update with wrong expected_version raises VersionMismatchError."""
        update = TableUpdate(
            question="Updated",
            context=None,
            status=TableStatus.OPEN,
        )

        with pytest.raises(VersionMismatchError) as exc_info:
            prepare_versioned_update(sample_table, update, Version(1), datetime.now())

        assert exc_info.value.current_version == Version(3)
        assert exc_info.value.expected_version == Version(1)


# =============================================================================
# Shell: VersionConflictError Tests
# =============================================================================


class TestVersionConflictError:
    """Tests for VersionConflictError."""

    def test_error_attributes(self) -> None:
        """VersionConflictError has correct attributes."""
        error = VersionConflictError(TableId("t1"), Version(5), Version(3))

        assert error.table_id == TableId("t1")
        assert error.current_version == Version(5)
        assert error.expected_version == Version(3)

    def test_error_message(self) -> None:
        """VersionConflictError has descriptive message."""
        error = VersionConflictError(TableId("table-xyz"), Version(10), Version(5))

        assert "table-xyz" in str(error)
        assert "expected version 5" in str(error)
        assert "current is 10" in str(error)

    def test_to_json(self) -> None:
        """VersionConflictError can serialize to JSON."""
        error = VersionConflictError(TableId("table-123"), Version(7), Version(4))

        json_data = error.to_json()

        assert json_data["error"] == "version_conflict"
        assert json_data["table_id"] == "table-123"
        assert json_data["current_version"] == 7
        assert json_data["expected_version"] == 4
        assert "message" in json_data

    def test_json_example(self) -> None:
        """VersionConflictError JSON example for documentation.

        This is the JSON format returned to API consumers when a
        version conflict occurs during optimistic concurrency update.
        """
        error = VersionConflictError(TableId("table-abc"), Version(5), Version(2))
        json_data = error.to_json()

        # Verify the JSON structure
        expected_keys = {"error", "table_id", "current_version", "expected_version", "message"}
        assert set(json_data.keys()) == expected_keys


# =============================================================================
# Shell: Repository Integration Tests
# =============================================================================


class TestUpdateTableWithVersion:
    """Tests for update_table with optimistic concurrency."""

    @pytest.fixture
    def db(self) -> sqlite3.Connection:
        """Create an in-memory database with tables schema."""
        conn = sqlite3.connect(":memory:")
        create_tables_table(conn)
        return conn

    @pytest.fixture
    def sample_table(self) -> Table:
        """Create a sample table for testing."""
        return Table(
            id=TableId("test-table"),
            question="Test question",
            context="Test context",
            status=TableStatus.OPEN,
            version=Version(1),
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            updated_at=datetime(2024, 1, 1, 12, 0, 0),
        )

    def test_update_with_correct_version_succeeds(
        self, db: sqlite3.Connection, sample_table: Table
    ) -> None:
        """Update with correct version succeeds."""
        # Create table first
        create_table(db, sample_table)

        # Update with correct version
        update = TableUpdate(
            question="Updated question",
            context="Updated context",
            status=TableStatus.PAUSED,
        )
        now = datetime(2024, 1, 2, 12, 0, 0)

        result = update_table(db, TableId("test-table"), update, Version(1), now)

        assert isinstance(result, Success)
        updated = result.unwrap()
        assert updated.question == "Updated question"
        assert updated.version == Version(2)

    def test_update_with_wrong_version_returns_conflict(
        self, db: sqlite3.Connection, sample_table: Table
    ) -> None:
        """Update with wrong version returns VersionConflictError."""
        create_table(db, sample_table)

        update = TableUpdate(
            question="Should not apply",
            context=None,
            status=TableStatus.OPEN,
        )

        # Wrong version (1 instead of actual 1, but let's make it 0)
        result = update_table(db, TableId("test-table"), update, Version(999), datetime.now())

        assert isinstance(result, Failure)
        error = result.failure()
        assert isinstance(error, VersionConflictError)
        assert error.current_version == Version(1)
        assert error.expected_version == Version(999)

    def test_update_nonexistent_table_returns_not_found(self, db: sqlite3.Connection) -> None:
        """Update of nonexistent table returns TableNotFoundError."""
        update = TableUpdate(
            question="Question",
            context=None,
            status=TableStatus.OPEN,
        )

        result = update_table(db, TableId("nonexistent"), update, Version(1), datetime.now())

        assert isinstance(result, Failure)
        assert isinstance(result.failure(), TableNotFoundError)

    def test_multiple_updates_increment_version(
        self, db: sqlite3.Connection, sample_table: Table
    ) -> None:
        """Multiple updates increment version each time."""
        create_table(db, sample_table)

        # First update
        update1 = TableUpdate(question="V2", context=None, status=TableStatus.OPEN)
        result1 = update_table(db, TableId("test-table"), update1, Version(1), datetime.now())
        assert isinstance(result1, Success)
        assert result1.unwrap().version == Version(2)

        # Second update
        update2 = TableUpdate(question="V3", context=None, status=TableStatus.OPEN)
        result2 = update_table(db, TableId("test-table"), update2, Version(2), datetime.now())
        assert isinstance(result2, Success)
        assert result2.unwrap().version == Version(3)

        # Third update
        update3 = TableUpdate(question="V4", context=None, status=TableStatus.OPEN)
        result3 = update_table(db, TableId("test-table"), update3, Version(3), datetime.now())
        assert isinstance(result3, Success)
        assert result3.unwrap().version == Version(4)


class TestReplaceOnlyBehavior:
    """Tests for replace-only behavior in repository."""

    @pytest.fixture
    def db(self) -> sqlite3.Connection:
        """Create an in-memory database."""
        conn = sqlite3.connect(":memory:")
        create_tables_table(conn)
        return conn

    @pytest.fixture
    def table_with_context(self) -> Table:
        """Create a table with context."""
        return Table(
            id=TableId("replace-test"),
            question="Original question",
            context="Original context that should be replaced",
            status=TableStatus.OPEN,
            version=Version(1),
            created_at=datetime(2024, 1, 1, 12, 0, 0),
            updated_at=datetime(2024, 1, 1, 12, 0, 0),
        )

    def test_replace_only_full_replacement(
        self, db: sqlite3.Connection, table_with_context: Table
    ) -> None:
        """Update replaces all fields, does not merge.

        This test verifies REPLACE-ONLY semantics:
        - Update MUST include all fields
        - Fields are replaced entirely (no partial patch)
        - Original fields are NOT preserved if not in update
        """
        create_table(db, table_with_context)

        # Update with DIFFERENT values for all fields
        update = TableUpdate(
            question="NEW question (replaced)",
            context="NEW context (replaced)",
            status=TableStatus.PAUSED,
        )

        result = update_table(db, TableId("replace-test"), update, Version(1), datetime.now())

        assert isinstance(result, Success)
        updated = result.unwrap()

        # All fields replaced
        assert updated.question == "NEW question (replaced)"
        assert updated.context == "NEW context (replaced)"
        assert updated.status == TableStatus.PAUSED

        # Verify in database
        fetch_result = get_table(db, TableId("replace-test"))
        assert isinstance(fetch_result, Success)
        fetched = fetch_result.unwrap()
        assert fetched.question == "NEW question (replaced)"
        assert fetched.context == "NEW context (replaced)"

    def test_replace_clears_context(
        self, db: sqlite3.Connection, table_with_context: Table
    ) -> None:
        """Replace can explicitly clear context by setting to None."""
        create_table(db, table_with_context)

        # Update with context=None to clear it
        update = TableUpdate(
            question="Updated question",
            context=None,  # Explicitly clear context
            status=TableStatus.OPEN,
        )

        result = update_table(db, TableId("replace-test"), update, Version(1), datetime.now())

        assert isinstance(result, Success)
        assert result.unwrap().context is None

    def test_original_not_preserved_if_different(
        self, db: sqlite3.Connection, table_with_context: Table
    ) -> None:
        """Original context is NOT preserved if update has different value.

        This confirms replace-only (not merge) semantics.
        """
        create_table(db, table_with_context)

        # Update with different question but also different context
        update = TableUpdate(
            question="Changed question",
            context="Changed context",  # Must explicitly set
            status=TableStatus.OPEN,
        )

        result = update_table(db, TableId("replace-test"), update, Version(1), datetime.now())

        assert isinstance(result, Success)
        updated = result.unwrap()

        # Original context was NOT preserved (replace-only)
        assert updated.context == "Changed context"
        assert updated.context != "Original context that should be replaced"


# =============================================================================
# Example: VersionConflictError JSON Output
# =============================================================================


def test_version_conflict_error_json_example():
    """Example of VersionConflictError JSON for documentation.

    This is the JSON structure returned when optimistic concurrency conflict occurs:

    ```json
    {
        "error": "version_conflict",
        "table_id": "table-123",
        "current_version": 5,
        "expected_version": 3,
        "message": "Version conflict for table table-123: expected version 3, but current is 5"
    }
    ```
    """
    error = VersionConflictError(TableId("table-123"), Version(5), Version(3))
    json_output = error.to_json()

    # Verify structure
    assert json_output == {
        "error": "version_conflict",
        "table_id": "table-123",
        "current_version": 5,
        "expected_version": 3,
        "message": "Version conflict for table table-123: expected version 3, but current is 5",
    }


# =============================================================================
# Doctest-style examples in code
# =============================================================================


def test_replace_only_vs_patch_semantics():
    """Documentation: Replace-only vs partial patch semantics.

    REPLACE-ONLY (what we implement):
    - Caller provides ALL updatable fields
    - Server replaces entire record
    - No field is implicitly preserved

    PARTIAL PATCH (what we DON'T do):
    - Caller provides subset of fields
    - Server merges with existing record
    - Unspecified fields are preserved
    """
    # Original table
    original = Table(
        id=TableId("example"),
        question="What is AI?",
        context="Focus on ethics",
        status=TableStatus.OPEN,
        version=Version(1),
        created_at=datetime(2024, 1, 1),
        updated_at=datetime(2024, 1, 1),
    )

    # Replace-only update: must provide ALL fields
    update = TableUpdate(
        question="What is Machine Learning?",  # Changed
        context="Focus on supervised learning",  # Changed
        status=TableStatus.PAUSED,  # Changed
    )

    result = prepare_table_update(original, update, datetime.now())

    # All fields are from update (replace-only)
    assert result.question == "What is Machine Learning?"
    assert result.context == "Focus on supervised learning"
    assert result.status == TableStatus.PAUSED
    # Original values are gone - replaced entirely
