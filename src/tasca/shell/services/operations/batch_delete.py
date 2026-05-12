"""Transport-neutral batch delete orchestration.

This module owns fetch, validation, and cascade-delete sequencing for both
REST and MCP adapters. Transports should only translate the typed outcome into
their local response envelope.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Literal

from returns.result import Failure, Result, Success

from tasca.core.domain.table import Table, TableId
from tasca.core.services.batch_delete_service import (
    MAX_BATCH_SIZE,
    BatchDeleteRejection,
    validate_batch_delete_request,
)
from tasca.shell.storage.table_repo import TableNotFoundError, batch_delete_tables, get_table

BatchDeleteStatus = Literal[
    "deleted",
    "invalid_request",
    "precondition_failed",
    "database_error",
]


@dataclass(frozen=True)
class BatchDeleteOperationResult:
    """Typed outcome for a batch-delete request."""

    status: BatchDeleteStatus
    requested_ids: list[str]
    deleted_ids: list[str] = field(default_factory=list)
    rejections: list[BatchDeleteRejection] = field(default_factory=list)
    max_batch_size: int = MAX_BATCH_SIZE
    error: str | None = None

    @property
    def is_success(self) -> bool:
        """Return whether the operation deleted all requested tables."""
        return self.status == "deleted"

    @property
    def rejection_details(self) -> list[dict[str, str]]:
        """Return transport-neutral rejection details derived from core validation."""
        return [{"id": rejection.table_id, "reason": rejection.reason} for rejection in self.rejections]


# @shell_complexity: 5 branches for input bounds + per-ID fetch + validation + delete + error disposition
def delete_tables_batch(
    conn: sqlite3.Connection,
    table_ids: list[str],
) -> Result[BatchDeleteOperationResult, BatchDeleteOperationResult]:
    """Fetch, validate, and all-or-nothing delete requested tables.

    The core validation service remains the only source of rejection reasons;
    this operation supplies the fetched tables and returns those typed reasons
    unchanged for transport rendering.
    """
    requested_ids = list(table_ids)
    if not requested_ids or len(requested_ids) > MAX_BATCH_SIZE:
        return Failure(BatchDeleteOperationResult(
            status="invalid_request",
            requested_ids=requested_ids,
            error=f"ids must contain 1 to {MAX_BATCH_SIZE} table IDs.",
        ))

    tables_for_validation: list[Table] = []
    for table_id in requested_ids:
        result = get_table(conn, TableId(table_id))
        if isinstance(result, Success):
            tables_for_validation.append(result.unwrap())
        elif not isinstance(result.failure(), TableNotFoundError):
            return Failure(BatchDeleteOperationResult(
                status="database_error",
                requested_ids=requested_ids,
                error=f"Failed to get table {table_id}: {result.failure()}",
            ))

    validation = validate_batch_delete_request(tables_for_validation, requested_ids)
    if not validation.is_valid:
        return Failure(BatchDeleteOperationResult(
            status="precondition_failed",
            requested_ids=requested_ids,
            rejections=validation.rejections,
        ))

    delete_result = batch_delete_tables(conn, validation.valid_ids)
    if isinstance(delete_result, Failure):
        return Failure(BatchDeleteOperationResult(
            status="database_error",
            requested_ids=requested_ids,
            error=str(delete_result.failure()),
        ))

    return Success(BatchDeleteOperationResult(
        status="deleted",
        requested_ids=requested_ids,
        deleted_ids=delete_result.unwrap(),
    ))
