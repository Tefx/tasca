"""
Batch delete service - core validation for batch table deletion.

Pure validation functions. No I/O.
"""

from dataclasses import dataclass

import deal

from tasca.core.domain.table import Table, TableStatus

# Max tables per batch delete request
MAX_BATCH_SIZE = 100


@dataclass(frozen=True)
class BatchDeleteRejection:
    """A single rejection reason for a table ID in a batch delete request.

    >>> r = BatchDeleteRejection(table_id="t1", reason="NOT_FOUND")
    >>> r.table_id
    't1'
    >>> r.reason
    'NOT_FOUND'
    """

    table_id: str
    reason: str  # "NOT_FOUND" | "TABLE_NOT_CLOSED"


@dataclass(frozen=True)
class BatchDeleteValidation:
    """Result of validating a batch delete request.

    >>> v = BatchDeleteValidation(valid_ids=["t1"], rejections=[])
    >>> v.is_valid
    True
    >>> v2 = BatchDeleteValidation(valid_ids=[], rejections=[BatchDeleteRejection("t2", "NOT_FOUND")])
    >>> v2.is_valid
    False
    """

    valid_ids: list[str]
    rejections: list[BatchDeleteRejection]

    @property
    @deal.post(lambda result: isinstance(result, bool))
    def is_valid(self) -> bool:
        """True if all requested IDs passed validation (no rejections).

        >>> BatchDeleteValidation(valid_ids=["a"], rejections=[]).is_valid
        True
        >>> BatchDeleteValidation(valid_ids=[], rejections=[BatchDeleteRejection("a", "NOT_FOUND")]).is_valid
        False
        """
        return len(self.rejections) == 0


@deal.pre(lambda tables, table_ids: len(tables) <= len(table_ids) and 0 < len(table_ids) <= MAX_BATCH_SIZE)
@deal.post(lambda result: isinstance(result, BatchDeleteValidation))
def validate_batch_delete_request(
    tables: list[Table],
    table_ids: list[str],
) -> BatchDeleteValidation:
    """Validate that all requested table IDs are deletable.

    Checks:
    - Each ID must exist in the provided tables list
    - Each matching table must have status CLOSED

    All-or-nothing: if any ID fails validation, the entire batch is rejected.

    Args:
        tables: The current tables from the database.
        table_ids: The IDs requested for deletion.

    Returns:
        BatchDeleteValidation with valid_ids and rejections.

    >>> from datetime import datetime
    >>> from tasca.core.domain.table import TableId, Version
    >>> closed = Table(
    ...     id=TableId("t1"), question="Q", context=None,
    ...     status=TableStatus.CLOSED, version=Version(1),
    ...     created_at=datetime(2024, 1, 1), updated_at=datetime(2024, 1, 1)
    ... )
    >>> result = validate_batch_delete_request([closed], ["t1"])
    >>> result.is_valid
    True
    >>> result.valid_ids
    ['t1']

    >>> open_t = Table(
    ...     id=TableId("t2"), question="Q", context=None,
    ...     status=TableStatus.OPEN, version=Version(1),
    ...     created_at=datetime(2024, 1, 1), updated_at=datetime(2024, 1, 1)
    ... )
    >>> result = validate_batch_delete_request([open_t], ["t2"])
    >>> result.is_valid
    False
    >>> result.rejections[0].reason
    'TABLE_NOT_CLOSED'

    >>> result = validate_batch_delete_request([closed], ["missing"])
    >>> result.is_valid
    False
    >>> result.rejections[0].reason
    'NOT_FOUND'

    >>> result = validate_batch_delete_request([closed, open_t], ["t1", "t2", "t3"])
    >>> result.is_valid
    False
    >>> sorted([(r.table_id, r.reason) for r in result.rejections])
    [('t2', 'TABLE_NOT_CLOSED'), ('t3', 'NOT_FOUND')]
    """
    table_map = {str(t.id): t for t in tables}
    valid_ids: list[str] = []
    rejections: list[BatchDeleteRejection] = []

    for tid in table_ids:
        table = table_map.get(tid)
        if table is None:
            rejections.append(BatchDeleteRejection(table_id=tid, reason="NOT_FOUND"))
        elif table.status != TableStatus.CLOSED:
            rejections.append(
                BatchDeleteRejection(table_id=tid, reason="TABLE_NOT_CLOSED")
            )
        else:
            valid_ids.append(tid)

    return BatchDeleteValidation(valid_ids=valid_ids, rejections=rejections)
