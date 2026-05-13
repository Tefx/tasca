"""Regression tests for pure storage row adapters."""

from __future__ import annotations

import pytest
from deal import PreContractError

from tasca.core.domain.table import TableStatus
from tasca.core.storage_rows import row_to_table


def test_row_to_table_rejects_none_status_before_enum_conversion() -> None:
    """A row with status=None is outside the adapter contract."""
    row = (
        "t1",
        "Question?",
        None,
        None,
        1,
        "2026-01-01T00:00:00",
        "2026-01-01T00:00:00",
    )

    with pytest.raises(PreContractError):
        row_to_table(row)


def test_row_to_table_converts_valid_table_row() -> None:
    """A valid SQLite table row is converted to the Table domain model."""
    row = (
        "t1",
        "Question?",
        "Context",
        "paused",
        "2",
        "2026-01-01T00:00:00",
        "2026-01-02T00:00:00",
        "creator-1",
        '["host-1", "host-2"]',
    )

    table = row_to_table(row)

    assert table.id == "t1"
    assert table.question == "Question?"
    assert table.context == "Context"
    assert table.status is TableStatus.PAUSED
    assert table.version == 2
    assert table.creator_patron_id == "creator-1"
    assert table.host_ids == ["host-1", "host-2"]
