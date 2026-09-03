"""SQLite reads for saying attachment metadata and explicit bodies."""

from __future__ import annotations

import sqlite3
from typing import NewType

from returns.result import Failure, Result, Success

from tasca.core.domain.saying import AttachmentSummary, SayingAttachment
from tasca.core.storage_rows import row_to_attachment, row_to_attachment_summary

AttachmentError = NewType("AttachmentError", str)


def list_attachment_summaries(
    conn: sqlite3.Connection,
    saying_ids: list[str],
) -> Result[dict[str, list[AttachmentSummary]], AttachmentError]:
    """Batch-load ordered metadata without selecting attachment bodies."""
    if not saying_ids:
        return Success({})
    try:
        placeholders = ",".join("?" * len(saying_ids))
        rows = conn.execute(
            f"""
            SELECT id, saying_id, name, position, byte_size
            FROM saying_attachments
            WHERE saying_id IN ({placeholders})
            ORDER BY saying_id, position
            """,  # noqa: S608
            saying_ids,
        ).fetchall()
        grouped: dict[str, list[AttachmentSummary]] = {}
        for attachment_id, saying_id, name, position, byte_size in rows:
            grouped.setdefault(str(saying_id), []).append(
                row_to_attachment_summary((attachment_id, saying_id, name, position, byte_size))
            )
        return Success(grouped)
    except sqlite3.Error as exc:
        return Failure(AttachmentError(f"Database error: {exc}"))


def list_full_attachments_by_table(
    conn: sqlite3.Connection,
    table_id: str,
) -> Result[dict[str, list[SayingAttachment]], AttachmentError]:
    """Batch-load complete ordered attachment bodies for one export."""
    try:
        rows = conn.execute(
            """
            SELECT a.id, a.saying_id, s.table_id, a.name, a.position, a.byte_size, a.content
            FROM saying_attachments AS a
            JOIN sayings AS s ON s.id = a.saying_id
            WHERE s.table_id = ?
            ORDER BY s.sequence, a.position
            """,
            (table_id,),
        ).fetchall()
        grouped: dict[str, list[SayingAttachment]] = {}
        for row in rows:
            attachment = row_to_attachment(row)
            grouped.setdefault(str(attachment.saying_id), []).append(attachment)
        return Success(grouped)
    except sqlite3.Error as exc:
        return Failure(AttachmentError(f"Database error: {exc}"))


def get_attachment_for_saying(
    conn: sqlite3.Connection,
    table_id: str,
    saying_id: str,
    attachment_id: str,
) -> Result[SayingAttachment | None, AttachmentError]:
    """Read one body only when all nested resource identifiers match."""
    try:
        row = conn.execute(
            """
            SELECT a.id, a.saying_id, s.table_id, a.name, a.position, a.byte_size, a.content
            FROM saying_attachments AS a
            JOIN sayings AS s ON s.id = a.saying_id
            WHERE a.id = ? AND a.saying_id = ? AND s.table_id = ?
            """,
            (attachment_id, saying_id, table_id),
        ).fetchone()
        return Success(row_to_attachment(row) if row is not None else None)
    except sqlite3.Error as exc:
        return Failure(AttachmentError(f"Database error: {exc}"))


def get_attachments_by_ids(
    conn: sqlite3.Connection,
    attachment_ids: list[str],
) -> Result[tuple[list[SayingAttachment], list[str]], AttachmentError]:
    """Batch-read bodies by ID, preserving input order and reporting missing IDs."""
    if not attachment_ids:
        return Success(([], []))
    try:
        placeholders = ",".join("?" * len(attachment_ids))
        rows = conn.execute(
            f"""
            SELECT a.id, a.saying_id, s.table_id, a.name, a.position, a.byte_size, a.content
            FROM saying_attachments AS a
            JOIN sayings AS s ON s.id = a.saying_id
            WHERE a.id IN ({placeholders})
            """,  # noqa: S608
            attachment_ids,
        ).fetchall()
        by_id = {str(row[0]): row_to_attachment(row) for row in rows}
        attachments = [
            by_id[attachment_id] for attachment_id in attachment_ids if attachment_id in by_id
        ]
        missing = [attachment_id for attachment_id in attachment_ids if attachment_id not in by_id]
        return Success((attachments, missing))
    except sqlite3.Error as exc:
        return Failure(AttachmentError(f"Database error: {exc}"))
