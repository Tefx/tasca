"""
Unit tests for CLI export command.

Tests the 'tasca export' command functionality:
1) correct markdown output to stdout
2) correct jsonl output to stdout
3) file output with -o
4) error on missing table (exit code 1)
5) default format is markdown

Uses in-memory SQLite DB with fixtures similar to existing test patterns.
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
import tempfile
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest

from tasca.cli import cmd_export, main
from tasca.core.domain.patron import PatronId
from tasca.core.domain.saying import Speaker, SpeakerKind
from tasca.core.domain.table import Table, TableId, TableStatus, Version
from tasca.shell.storage.database import apply_schema
from tasca.shell.storage.saying_repo import append_saying
from tasca.shell.storage.table_repo import create_table


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def test_db() -> Generator[sqlite3.Connection, None, None]:
    """Create an in-memory database with tables schema."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    apply_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def temp_output_file() -> Generator[Path, None, None]:
    """Create a temporary file for output testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "output.md"


# =============================================================================
# Helper Functions
# =============================================================================


def create_test_table(
    conn: sqlite3.Connection,
    table_id: str,
    question: str,
    context: str | None = None,
    status: TableStatus = TableStatus.OPEN,
) -> Table:
    """Create a test table directly in the database."""
    now = datetime.now(timezone.utc)
    table = Table(
        id=TableId(table_id),
        question=question,
        context=context,
        status=status,
        version=Version(1),
        created_at=now,
        updated_at=now,
    )
    result = create_table(conn, table)
    return result.unwrap()


def create_test_saying(
    conn: sqlite3.Connection,
    table_id: str,
    content: str,
    speaker_name: str = "Test Speaker",
    speaker_kind: SpeakerKind = SpeakerKind.HUMAN,
    patron_id: PatronId | None = None,
) -> None:
    """Create a test saying directly in the database."""
    speaker = Speaker(kind=speaker_kind, name=speaker_name, patron_id=patron_id)
    result = append_saying(conn, table_id, speaker, content)
    result.unwrap()


# =============================================================================
# Test: Markdown Output to Stdout
# =============================================================================


class TestExportMarkdownStdout:
    """Tests for markdown output to stdout."""

    def test_export_markdown_to_stdout(self, test_db: sqlite3.Connection) -> None:
        """Export markdown format to stdout works correctly."""
        table = create_test_table(
            test_db, "test-table-1", "What is the best approach?", context="Consider performance"
        )
        create_test_saying(test_db, table.id, "First message", speaker_name="Alice")
        create_test_saying(test_db, table.id, "Second message", speaker_name="Bob")

        args = argparse.Namespace(
            table_id=table.id,
            format="md",
            output=None,
        )

        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout):
            with redirect_stderr(stderr):
                with patch("tasca.cli.settings") as mock_settings:
                    # Create an in-memory DB path for testing
                    mock_settings.db_path = ":memory:"
                    # We need to patch the connection to use our test DB
                    with patch("sqlite3.connect", return_value=test_db):
                        result = cmd_export(args)

        assert result == 0

        output = stdout.getvalue()
        # Check markdown format
        assert "# What is the best approach?" in output
        assert f"| **Table** | `{table.id}` |" in output
        assert "| **Status** | open |" in output
        assert "## Transcript" in output
        # Check sayings are included
        assert "Alice" in output
        assert "Bob" in output
        assert "First message" in output
        assert "Second message" in output

    def test_export_markdown_empty_table(self, test_db: sqlite3.Connection) -> None:
        """Export empty table produces valid markdown."""
        table = create_test_table(test_db, "empty-table", "Empty question?")

        args = argparse.Namespace(
            table_id=table.id,
            format="md",
            output=None,
        )

        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout):
            with redirect_stderr(stderr):
                with patch("tasca.cli.settings") as mock_settings:
                    mock_settings.db_path = ":memory:"
                    with patch("sqlite3.connect", return_value=test_db):
                        result = cmd_export(args)

        assert result == 0

        output = stdout.getvalue()
        assert "# Empty question?" in output
        assert "_No sayings yet._" in output


# =============================================================================
# Test: JSONL Output to Stdout
# =============================================================================


class TestExportJsonlStdout:
    """Tests for JSONL output to stdout."""

    def test_export_jsonl_to_stdout(self, test_db: sqlite3.Connection) -> None:
        """Export JSONL format to stdout works correctly."""
        table = create_test_table(
            test_db, "test-table-2", "Discussion topic?", context="Context info"
        )
        create_test_saying(test_db, table.id, "Hello world", speaker_name="Alice")

        args = argparse.Namespace(
            table_id=table.id,
            format="jsonl",
            output=None,
        )

        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout):
            with redirect_stderr(stderr):
                with patch("tasca.cli.settings") as mock_settings:
                    mock_settings.db_path = ":memory:"
                    with patch("sqlite3.connect", return_value=test_db):
                        result = cmd_export(args)

        assert result == 0

        output = stdout.getvalue()
        lines = output.strip().split("\n")

        # Should have 3 lines: header + table + saying
        assert len(lines) == 3

        # Parse and validate header
        header = json.loads(lines[0])
        assert header["type"] == "export_header"
        assert header["table_id"] == table.id
        assert "exported_at" in header

        # Parse and validate table
        table_line = json.loads(lines[1])
        assert table_line["type"] == "table"
        assert table_line["table"]["id"] == table.id
        assert table_line["table"]["question"] == "Discussion topic?"
        assert table_line["table"]["context"] == "Context info"

        # Parse and validate saying
        saying_line = json.loads(lines[2])
        assert saying_line["type"] == "saying"
        assert saying_line["saying"]["table_id"] == table.id
        assert saying_line["saying"]["content"] == "Hello world"
        assert saying_line["saying"]["speaker"]["name"] == "Alice"

    def test_export_jsonl_multiple_sayings(self, test_db: sqlite3.Connection) -> None:
        """Export JSONL with multiple sayings maintains order."""
        table = create_test_table(test_db, "test-table-3", "Question?")
        create_test_saying(test_db, table.id, "First", speaker_name="A")
        create_test_saying(test_db, table.id, "Second", speaker_name="B")
        create_test_saying(test_db, table.id, "Third", speaker_name="C")

        args = argparse.Namespace(
            table_id=table.id,
            format="jsonl",
            output=None,
        )

        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout):
            with redirect_stderr(stderr):
                with patch("tasca.cli.settings") as mock_settings:
                    mock_settings.db_path = ":memory:"
                    with patch("sqlite3.connect", return_value=test_db):
                        result = cmd_export(args)

        assert result == 0

        output = stdout.getvalue()
        lines = output.strip().split("\n")

        # Header + table + 3 sayings
        assert len(lines) == 5

        # Verify sayings are in sequence order
        for i, line in enumerate(lines[2:], start=0):
            saying = json.loads(line)
            assert saying["saying"]["sequence"] == i


# =============================================================================
# Test: File Output with -o
# =============================================================================


class TestExportFileOutput:
    """Tests for file output with -o option."""

    def test_export_markdown_to_file(
        self, test_db: sqlite3.Connection, temp_output_file: Path
    ) -> None:
        """Export markdown to file with -o option."""
        table = create_test_table(test_db, "file-table-1", "File export question?")
        create_test_saying(test_db, table.id, "Test content", speaker_name="Writer")

        args = argparse.Namespace(
            table_id=table.id,
            format="md",
            output=str(temp_output_file),
        )

        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout):
            with redirect_stderr(stderr):
                with patch("tasca.cli.settings") as mock_settings:
                    mock_settings.db_path = ":memory:"
                    with patch("sqlite3.connect", return_value=test_db):
                        result = cmd_export(args)

        assert result == 0

        # Verify file was created
        assert temp_output_file.exists()

        # Verify file contents
        content = temp_output_file.read_text(encoding="utf-8")
        assert "# File export question?" in content
        assert "Test content" in content
        assert "Writer" in content

        # Stdout should be empty (output went to file)
        assert stdout.getvalue() == ""

    def test_export_jsonl_to_file(
        self, test_db: sqlite3.Connection, temp_output_file: Path
    ) -> None:
        """Export JSONL to file with -o option."""
        table = create_test_table(test_db, "file-table-2", "JSONL export?")
        create_test_saying(test_db, table.id, "JSON content", speaker_name="Bot")

        args = argparse.Namespace(
            table_id=table.id,
            format="jsonl",
            output=str(temp_output_file),
        )

        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout):
            with redirect_stderr(stderr):
                with patch("tasca.cli.settings") as mock_settings:
                    mock_settings.db_path = ":memory:"
                    with patch("sqlite3.connect", return_value=test_db):
                        result = cmd_export(args)

        assert result == 0

        # Verify file was created and contains valid JSONL
        assert temp_output_file.exists()
        content = temp_output_file.read_text(encoding="utf-8")
        lines = content.strip().split("\n")

        # Validate JSON structure
        header = json.loads(lines[0])
        assert header["type"] == "export_header"

        table_line = json.loads(lines[1])
        assert table_line["type"] == "table"

        saying_line = json.loads(lines[2])
        assert saying_line["saying"]["content"] == "JSON content"

    def test_export_creates_parent_directories(
        self, test_db: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """Export creates parent directories if they don't exist."""
        table = create_test_table(test_db, "nested-table", "Nested?")
        nested_file = tmp_path / "subdir" / "deep" / "output.md"

        args = argparse.Namespace(
            table_id=table.id,
            format="md",
            output=str(nested_file),
        )

        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout):
            with redirect_stderr(stderr):
                with patch("tasca.cli.settings") as mock_settings:
                    mock_settings.db_path = ":memory:"
                    with patch("sqlite3.connect", return_value=test_db):
                        result = cmd_export(args)

        assert result == 0
        assert nested_file.exists()
        assert nested_file.parent.exists()


# =============================================================================
# Test: Error on Missing Table (Exit Code 1)
# =============================================================================


class TestExportErrors:
    """Tests for error handling."""

    def test_export_missing_table_returns_exit_code_1(self, test_db: sqlite3.Connection) -> None:
        """Export non-existent table returns exit code 1."""
        args = argparse.Namespace(
            table_id="nonexistent-table-id",
            format="md",
            output=None,
        )

        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout):
            with redirect_stderr(stderr):
                with patch("tasca.cli.settings") as mock_settings:
                    mock_settings.db_path = ":memory:"
                    with patch("sqlite3.connect", return_value=test_db):
                        result = cmd_export(args)

        assert result == 1
        assert "not found" in stderr.getvalue().lower()

    def test_export_missing_table_via_main(self, test_db: sqlite3.Connection) -> None:
        """Export via main() with missing table returns exit code 1."""
        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout):
            with redirect_stderr(stderr):
                with patch("tasca.cli.settings") as mock_settings:
                    mock_settings.db_path = ":memory:"
                    with patch("sqlite3.connect", return_value=test_db):
                        result = main(["export", "nonexistent-table"])

        assert result == 1
        assert "not found" in stderr.getvalue().lower()


# =============================================================================
# Test: Default Format is Markdown
# =============================================================================


class TestExportDefaultFormat:
    """Tests for default format behavior."""

    def test_default_format_is_markdown(self, test_db: sqlite3.Connection) -> None:
        """When format is not specified, default is markdown."""
        table = create_test_table(test_db, "default-table", "Default format?")

        # Note: argparse sets default to "md" in cli.py
        args = argparse.Namespace(
            table_id=table.id,
            format="md",  # Default value from argparse
            output=None,
        )

        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout):
            with redirect_stderr(stderr):
                with patch("tasca.cli.settings") as mock_settings:
                    mock_settings.db_path = ":memory:"
                    with patch("sqlite3.connect", return_value=test_db):
                        result = cmd_export(args)

        assert result == 0

        output = stdout.getvalue()
        # Markdown should contain heading with question
        assert "# Default format?" in output
        # JSONL would start with {"type": but markdown doesn't
        assert not output.startswith('{"type":')

    def test_main_export_default_format(self, test_db: sqlite3.Connection) -> None:
        """Export via main() uses default markdown format."""
        table = create_test_table(test_db, "main-default-table", "Main default?")

        stdout = io.StringIO()
        stderr = io.StringIO()

        with redirect_stdout(stdout):
            with redirect_stderr(stderr):
                with patch("tasca.cli.settings") as mock_settings:
                    mock_settings.db_path = ":memory:"
                    with patch("sqlite3.connect", return_value=test_db):
                        # No --format flag, should default to markdown
                        result = main(["export", table.id])

        assert result == 0

        output = stdout.getvalue()
        assert "# Main default?" in output
        assert "## Transcript" in output


# =============================================================================
# Test: Argument Parsing for Export Command
# =============================================================================


class TestExportArgumentParsing:
    """Tests for export command argument parsing."""

    def test_parse_export_minimal(self) -> None:
        """Parse export with minimal required args."""
        with patch("tasca.cli.cmd_export") as mock_cmd_export:
            mock_cmd_export.return_value = 0
            result = main(["export", "my-table-id"])

            assert result == 0
            call_args = mock_cmd_export.call_args
            assert call_args is not None
            args = call_args[0][0]
            assert args.table_id == "my-table-id"
            assert args.format == "md"  # Default
            assert args.output is None

    def test_parse_export_with_format(self) -> None:
        """Parse export with format option."""
        with patch("tasca.cli.cmd_export") as mock_cmd_export:
            mock_cmd_export.return_value = 0
            result = main(["export", "my-table-id", "--format", "jsonl"])

            assert result == 0
            call_args = mock_cmd_export.call_args
            assert call_args is not None
            args = call_args[0][0]
            assert args.table_id == "my-table-id"
            assert args.format == "jsonl"

    def test_parse_export_with_short_format(self) -> None:
        """Parse export with short format option."""
        with patch("tasca.cli.cmd_export") as mock_cmd_export:
            mock_cmd_export.return_value = 0
            result = main(["export", "my-table-id", "-f", "jsonl"])

            assert result == 0
            call_args = mock_cmd_export.call_args
            assert call_args is not None
            args = call_args[0][0]
            assert args.format == "jsonl"

    def test_parse_export_with_output(self) -> None:
        """Parse export with output file option."""
        with patch("tasca.cli.cmd_export") as mock_cmd_export:
            mock_cmd_export.return_value = 0
            result = main(["export", "my-table-id", "-o", "/tmp/export.md"])

            assert result == 0
            call_args = mock_cmd_export.call_args
            assert call_args is not None
            args = call_args[0][0]
            assert args.output == "/tmp/export.md"

    def test_parse_export_with_all_options(self) -> None:
        """Parse export with all options."""
        with patch("tasca.cli.cmd_export") as mock_cmd_export:
            mock_cmd_export.return_value = 0
            result = main(
                [
                    "export",
                    "my-table-id",
                    "--format",
                    "jsonl",
                    "--output",
                    "/tmp/export.jsonl",
                ]
            )

            assert result == 0
            call_args = mock_cmd_export.call_args
            assert call_args is not None
            args = call_args[0][0]
            assert args.table_id == "my-table-id"
            assert args.format == "jsonl"
            assert args.output == "/tmp/export.jsonl"

    def test_parse_export_help(self) -> None:
        """Parse export --help shows usage."""
        with pytest.raises(SystemExit) as exc_info:
            main(["export", "--help"])
        assert exc_info.value.code == 0
