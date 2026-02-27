"""Unit tests for batch delete validation service."""

from datetime import datetime

import pytest

from tasca.core.domain.table import Table, TableId, TableStatus, Version
from tasca.core.services.batch_delete_service import (
    MAX_BATCH_SIZE,
    BatchDeleteRejection,
    BatchDeleteValidation,
    validate_batch_delete_request,
)


# =============================================================================
# Fixtures
# =============================================================================


def _make_table(
    table_id: str, status: TableStatus = TableStatus.CLOSED
) -> Table:
    """Create a test table with given ID and status."""
    return Table(
        id=TableId(table_id),
        question="Test question",
        context=None,
        status=status,
        version=Version(1),
        created_at=datetime(2024, 1, 1),
        updated_at=datetime(2024, 1, 1),
    )


# =============================================================================
# Happy path: all closed tables
# =============================================================================


class TestHappyPath:
    def test_single_closed_table(self):
        tables = [_make_table("t1")]
        result = validate_batch_delete_request(tables, ["t1"])
        assert result.is_valid
        assert result.valid_ids == ["t1"]
        assert result.rejections == []

    def test_multiple_closed_tables(self):
        tables = [_make_table(f"t{i}") for i in range(5)]
        ids = [f"t{i}" for i in range(5)]
        result = validate_batch_delete_request(tables, ids)
        assert result.is_valid
        assert result.valid_ids == ids

    def test_subset_of_available_tables(self):
        tables = [_make_table(f"t{i}") for i in range(10)]
        result = validate_batch_delete_request(tables, ["t3", "t7"])
        assert result.is_valid
        assert result.valid_ids == ["t3", "t7"]


# =============================================================================
# Rejection: mixed statuses
# =============================================================================


class TestRejections:
    def test_open_table_rejected(self):
        tables = [_make_table("t1", TableStatus.OPEN)]
        result = validate_batch_delete_request(tables, ["t1"])
        assert not result.is_valid
        assert len(result.rejections) == 1
        assert result.rejections[0].reason == "TABLE_NOT_CLOSED"

    def test_paused_table_rejected(self):
        tables = [_make_table("t1", TableStatus.PAUSED)]
        result = validate_batch_delete_request(tables, ["t1"])
        assert not result.is_valid
        assert result.rejections[0].reason == "TABLE_NOT_CLOSED"

    def test_not_found_rejected(self):
        tables = [_make_table("t1")]
        result = validate_batch_delete_request(tables, ["missing"])
        assert not result.is_valid
        assert result.rejections[0].reason == "NOT_FOUND"

    def test_mixed_valid_and_invalid(self):
        tables = [
            _make_table("t1", TableStatus.CLOSED),
            _make_table("t2", TableStatus.OPEN),
        ]
        result = validate_batch_delete_request(tables, ["t1", "t2", "t3"])
        assert not result.is_valid
        # t1 is valid, t2 is not closed, t3 is not found
        assert result.valid_ids == ["t1"]
        reasons = {r.table_id: r.reason for r in result.rejections}
        assert reasons == {"t2": "TABLE_NOT_CLOSED", "t3": "NOT_FOUND"}

    def test_all_not_found(self):
        result = validate_batch_delete_request([], ["a", "b", "c"])
        assert not result.is_valid
        assert len(result.rejections) == 3
        assert all(r.reason == "NOT_FOUND" for r in result.rejections)


# =============================================================================
# Boundary: batch size limits
# =============================================================================


class TestBatchSizeBoundary:
    def test_exactly_max_batch_size(self):
        tables = [_make_table(f"t{i}") for i in range(MAX_BATCH_SIZE)]
        ids = [f"t{i}" for i in range(MAX_BATCH_SIZE)]
        result = validate_batch_delete_request(tables, ids)
        assert result.is_valid
        assert len(result.valid_ids) == MAX_BATCH_SIZE

    def test_exceeds_max_batch_size(self):
        tables = [_make_table(f"t{i}") for i in range(MAX_BATCH_SIZE + 1)]
        ids = [f"t{i}" for i in range(MAX_BATCH_SIZE + 1)]
        with pytest.raises(Exception):  # deal.PreContractError
            validate_batch_delete_request(tables, ids)

    def test_empty_list_rejected_by_contract(self):
        with pytest.raises(Exception):  # deal.PreContractError
            validate_batch_delete_request([], [])

    def test_single_id_minimum(self):
        tables = [_make_table("t1")]
        result = validate_batch_delete_request(tables, ["t1"])
        assert result.is_valid

    def test_duplicate_ids_in_request(self):
        """SG-1: Duplicate IDs produce duplicate valid_ids (idempotent delete)."""
        tables = [_make_table("t1")]
        result = validate_batch_delete_request(tables, ["t1", "t1"])
        assert result.is_valid
        assert result.valid_ids == ["t1", "t1"]


# =============================================================================
# Data class behavior
# =============================================================================


class TestDataClasses:
    def test_rejection_is_frozen(self):
        r = BatchDeleteRejection(table_id="t1", reason="NOT_FOUND")
        with pytest.raises(AttributeError):
            r.table_id = "t2"  # type: ignore[misc]

    def test_validation_is_frozen(self):
        v = BatchDeleteValidation(valid_ids=[], rejections=[])
        with pytest.raises(AttributeError):
            v.valid_ids = ["x"]  # type: ignore[misc]

    def test_is_valid_with_empty_valid_ids_and_rejections(self):
        # Edge: no IDs requested that matched, but also no rejections
        # This shouldn't happen in practice (precondition requires len > 0)
        v = BatchDeleteValidation(valid_ids=[], rejections=[])
        assert v.is_valid  # no rejections = valid
