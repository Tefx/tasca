"""Behavioral tests for Markdown attachment validation, storage, and export."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest
from returns.result import Failure, Success

from tasca.core.domain.saying import AttachmentInput, Speaker, SpeakerKind
from tasca.core.domain.table import Table, TableId, TableStatus
from tasca.core.services.attachment_service import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS_TOTAL_BYTES,
    SayingValidationKind,
    validate_saying_payload,
)
from tasca.core.services.limits_service import LimitError, LimitsConfig
from tasca.shell.services.operations.table_export import export_table
from tasca.shell.storage.attachment_repo import get_attachment_for_saying
from tasca.shell.storage.database import apply_schema
from tasca.shell.storage.saying_repo import (
    append_saying,
    get_table_content_bytes,
    list_sayings_by_table,
)
from tasca.shell.storage.table_repo import batch_delete_tables, create_table


def _database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    assert isinstance(apply_schema(conn), Success)
    table = Table(
        id=TableId("attachments-table"),
        question="How should attachments work?",
        status=TableStatus.OPEN,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assert isinstance(create_table(conn, table), Success)
    return conn


def _speaker() -> Speaker:
    return Speaker(kind=SpeakerKind.HUMAN, name="Alice")


@pytest.mark.parametrize(
    "name",
    [
        "",
        " notes.md",
        "notes.md ",
        "dir/notes.md",
        "dir\\notes.md",
        "nul\x00notes.md",
        "notes.txt",
        "NOTES.MD",
        f"{'a' * 126}.md",
    ],
)
def test_attachment_name_validation_is_strict(name: str) -> None:
    error = validate_saying_payload("Body", [AttachmentInput(name=name, content="note")])
    assert error is not None
    assert error.kind == SayingValidationKind.ATTACHMENT_NAME
    assert error.attachment_index == 0


def test_attachment_name_limit_counts_unicode_characters_inclusively() -> None:
    exact_name = f"{'😀' * 125}.md"
    assert len(exact_name) == 128
    assert (
        validate_saying_payload("Body", [AttachmentInput(name=exact_name, content="note")]) is None
    )


def test_unencodable_unicode_is_rejected_as_validation_failure() -> None:
    invalid_scalar = chr(0xD800)
    name_error = validate_saying_payload(
        "Body", [AttachmentInput(name=f"{invalid_scalar}.md", content="note")]
    )
    content_error = validate_saying_payload(
        "Body", [AttachmentInput(name="note.md", content=invalid_scalar)]
    )

    assert name_error is not None
    assert name_error.kind == SayingValidationKind.ATTACHMENT_NAME
    assert content_error is not None
    assert content_error.kind == SayingValidationKind.ATTACHMENT_CONTENT


def test_attachment_count_and_nonblank_content_are_validated() -> None:
    too_many = [AttachmentInput(name=f"{index}.md", content="x") for index in range(9)]
    count_error = validate_saying_payload("Body", too_many)
    body_error = validate_saying_payload(" \n", [])
    attachment_error = validate_saying_payload(
        "Body", [AttachmentInput(name="empty.md", content="\t")]
    )

    assert count_error is not None
    assert count_error.kind == SayingValidationKind.ATTACHMENT_COUNT
    assert count_error.actual == 9
    assert body_error is not None and body_error.kind == SayingValidationKind.CONTENT
    assert attachment_error is not None
    assert attachment_error.kind == SayingValidationKind.ATTACHMENT_CONTENT


def test_utf8_item_and_aggregate_boundaries_are_exact() -> None:
    exact_multibyte = "é" * (MAX_ATTACHMENT_BYTES // 2)
    assert (
        validate_saying_payload("Body", [AttachmentInput(name="exact.md", content=exact_multibyte)])
        is None
    )

    item_error = validate_saying_payload(
        "Body", [AttachmentInput(name="large.md", content=exact_multibyte + "é")]
    )
    exact_total = [
        AttachmentInput(name=f"{index}.md", content="x" * MAX_ATTACHMENT_BYTES)
        for index in range(4)
    ]
    assert validate_saying_payload("Body", exact_total) is None
    total_error = validate_saying_payload(
        "Body", [*exact_total, AttachmentInput(name="overflow.md", content="x")]
    )

    assert item_error is not None
    assert item_error.kind == SayingValidationKind.ATTACHMENT_BYTES
    assert item_error.actual == MAX_ATTACHMENT_BYTES + 2
    assert total_error is not None
    assert total_error.kind == SayingValidationKind.ATTACHMENT_TOTAL_BYTES
    assert total_error.actual == MAX_ATTACHMENTS_TOTAL_BYTES + 1


def test_atomic_insert_returns_metadata_and_explicit_read_returns_raw_body() -> None:
    conn = _database()
    attachments = [
        AttachmentInput(name="first.md", content="# First\n\n<script>x</script>"),
        AttachmentInput(name="日本語.markdown", content="```mermaid\ngraph TD\nA-->B\n```"),
    ]

    result = append_saying(conn, "attachments-table", _speaker(), "Body", attachments)

    assert isinstance(result, Success)
    saying = result.unwrap()
    assert [item.position for item in saying.attachments] == [0, 1]
    assert [item.name for item in saying.attachments] == ["first.md", "日本語.markdown"]
    assert saying.attachments[1].byte_size == len(attachments[1].content.encode("utf-8"))
    assert all(item.media_type == "text/markdown" for item in saying.attachments)
    assert "content" not in saying.attachments[0].model_dump()

    full = get_attachment_for_saying(
        conn,
        "attachments-table",
        str(saying.id),
        str(saying.attachments[0].id),
    ).unwrap()
    assert full is not None
    assert full.table_id == "attachments-table"
    assert full.saying_id == saying.id
    assert full.content == attachments[0].content
    assert (
        get_attachment_for_saying(
            conn, "wrong-table", str(saying.id), str(saying.attachments[0].id)
        ).unwrap()
        is None
    )


def test_second_attachment_failure_rolls_back_rows_and_sequence() -> None:
    conn = _database()
    conn.execute(
        """
        CREATE TRIGGER fail_second_attachment
        BEFORE INSERT ON saying_attachments
        WHEN NEW.position = 1
        BEGIN
            SELECT RAISE(ABORT, 'injected second attachment failure');
        END
        """
    )
    failed = append_saying(
        conn,
        "attachments-table",
        _speaker(),
        "Will roll back",
        [
            AttachmentInput(name="first.md", content="one"),
            AttachmentInput(name="second.md", content="two"),
        ],
    )

    assert isinstance(failed, Failure)
    assert conn.execute("SELECT COUNT(*) FROM sayings").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM saying_attachments").fetchone()[0] == 0

    conn.execute("DROP TRIGGER fail_second_attachment")
    retried = append_saying(conn, "attachments-table", _speaker(), "Retry")
    assert retried.unwrap().sequence == 0


def test_table_byte_limit_counts_exact_saying_and_attachment_utf8_bytes() -> None:
    conn = _database()
    limits = LimitsConfig(max_bytes_per_table=10)
    admitted = append_saying(
        conn,
        "attachments-table",
        _speaker(),
        "é",
        [AttachmentInput(name="eight.md", content="12345678")],
        limits,
    )
    rejected = append_saying(conn, "attachments-table", _speaker(), "x", limits=limits)

    assert isinstance(admitted, Success)
    assert get_table_content_bytes(conn, "attachments-table").unwrap() == 10
    assert isinstance(rejected, Failure)
    assert isinstance(rejected.failure(), LimitError)
    assert conn.execute("SELECT MAX(sequence) FROM sayings").fetchone()[0] == 0


def test_ordinary_reads_batch_metadata_without_selecting_attachment_content() -> None:
    conn = _database()
    for index in range(2):
        append_saying(
            conn,
            "attachments-table",
            _speaker(),
            f"Body {index}",
            [AttachmentInput(name=f"{index}.md", content=f"secret {index}")],
        ).unwrap()

    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    sayings = list_sayings_by_table(conn, "attachments-table").unwrap()
    conn.set_trace_callback(None)

    attachment_queries = [
        statement for statement in statements if "FROM SAYING_ATTACHMENTS" in statement.upper()
    ]
    assert len(attachment_queries) == 1
    assert " CONTENT" not in attachment_queries[0].upper()
    assert [item.attachments[0].name for item in sayings] == ["0.md", "1.md"]
    assert all("content" not in item.attachments[0].model_dump() for item in sayings)


def test_attachment_terms_are_excluded_from_saying_fts_and_table_delete_cascades() -> None:
    conn = _database()
    append_saying(
        conn,
        "attachments-table",
        _speaker(),
        "ordinary body",
        [AttachmentInput(name="search.md", content="attachmentonlyneedle")],
    ).unwrap()
    matches = conn.execute(
        "SELECT COUNT(*) FROM sayings_fts WHERE sayings_fts MATCH ?",
        ("attachmentonlyneedle",),
    ).fetchone()[0]
    assert matches == 0

    assert isinstance(batch_delete_tables(conn, ["attachments-table"]), Success)
    assert conn.execute("SELECT COUNT(*) FROM saying_attachments").fetchone()[0] == 0


def test_existing_database_migration_preserves_sayings_with_empty_metadata() -> None:
    conn = _database()
    saying = append_saying(conn, "attachments-table", _speaker(), "Before migration").unwrap()
    conn.execute("DROP TABLE saying_attachments")

    assert isinstance(apply_schema(conn), Success)
    loaded = list_sayings_by_table(conn, "attachments-table").unwrap()
    assert [item.id for item in loaded] == [saying.id]
    assert loaded[0].attachments == []


def test_exports_preserve_unicode_order_and_append_material_after_transcript() -> None:
    conn = _database()
    raw = "# 原文\n\n[unsafe](javascript:alert(1))\n\n```mermaid\ngraph TD\nA-->B\n```"
    append_saying(
        conn,
        "attachments-table",
        _speaker(),
        "Compact transcript body",
        [
            AttachmentInput(name="one.md", content=raw),
            AttachmentInput(name="two.markdown", content="Second attachment"),
        ],
    ).unwrap()

    jsonl = (
        export_table(
            conn,
            "attachments-table",
            "jsonl",
            exported_at="2026-01-01T00:00:00Z",
        )
        .unwrap()
        .content
    )
    assert jsonl is not None
    records = [json.loads(line) for line in jsonl.splitlines()]
    assert records[0]["export_version"] == "0.2"
    exported = records[2]["saying"]["attachments"]
    assert [item["position"] for item in exported] == [0, 1]
    assert exported[0]["content"] == raw
    assert exported[1]["content"] == "Second attachment"

    markdown = export_table(conn, "attachments-table", "markdown").unwrap().content
    assert markdown is not None
    transcript_offset = markdown.index("## Transcript")
    attachment_offset = markdown.index("## Attachments")
    assert attachment_offset > transcript_offset
    assert markdown.index(raw) > attachment_offset
    assert markdown.index("Second attachment") > markdown.index(raw)
