"""
Unit tests for CLI functionality.

This module tests the tasca new CLI command components:
- Argument parsing
- get_lan_ip() function
- create_table_directly() function
- print_startup_banner() function
- is_server_running() function (legacy, used by MCP mode)
- Error handling paths

Uses mocking for HTTP clients to avoid external dependencies.
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest

from tasca.cli import (
    create_table_directly,
    create_table_via_mcp,
    create_table_via_rest,
    get_lan_ip,
    is_server_running,
    main,
    cmd_new,
    print_startup_banner,
)

if TYPE_CHECKING:
    from collections.abc import Generator


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_stdout() -> Generator[io.StringIO, None, None]:
    """Capture stdout for testing CLI output."""
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        yield stdout


@pytest.fixture
def mock_stderr() -> Generator[io.StringIO, None, None]:
    """Capture stderr for testing CLI output."""
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        yield stderr


@pytest.fixture
def temp_db() -> Generator[Path, None, None]:
    """Create a temporary database file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test.db"


# =============================================================================
# Unit Tests: Argument Parsing
# =============================================================================


class TestArgumentParsing:
    """Tests for CLI argument parsing."""

    def test_parse_no_command_shows_help(self) -> None:
        """No command should show help and return 1."""
        with patch("sys.stdout", new_callable=io.StringIO):
            result = main([])
        assert result == 1  # No command -> help shown, exit 1

    def test_parse_new_minimal(self) -> None:
        """Parse 'new' with minimal required args."""
        with patch("tasca.cli.cmd_new") as mock_cmd_new:
            mock_cmd_new.return_value = 0
            result = main(["new", "What is the best approach?"])
            assert result == 0
            # Verify cmd_new was called with correct args
            call_args = mock_cmd_new.call_args
            assert call_args is not None
            args = call_args[0][0]
            assert args.question == "What is the best approach?"
            assert args.context is None
            assert args.host is None
            assert args.port is None

    def test_parse_new_with_context(self) -> None:
        """Parse 'new' with context option."""
        with patch("tasca.cli.cmd_new") as mock_cmd_new:
            mock_cmd_new.return_value = 0
            result = main(
                [
                    "new",
                    "What is the best approach?",
                    "-c",
                    "Consider performance",
                ]
            )
            assert result == 0
            call_args = mock_cmd_new.call_args
            assert call_args is not None
            args = call_args[0][0]
            assert args.question == "What is the best approach?"
            assert args.context == "Consider performance"

    def test_parse_new_with_host_port(self) -> None:
        """Parse 'new' with custom host and port."""
        with patch("tasca.cli.cmd_new") as mock_cmd_new:
            mock_cmd_new.return_value = 0
            result = main(
                [
                    "new",
                    "What is the best approach?",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "3000",
                ]
            )
            assert result == 0
            call_args = mock_cmd_new.call_args
            assert call_args is not None
            args = call_args[0][0]
            assert args.host == "127.0.0.1"
            assert args.port == 3000

    def test_parse_missing_question_exits(self) -> None:
        """Missing question argument should cause argparse to exit."""
        with pytest.raises(SystemExit) as exc_info:
            main(["new"])
        assert exc_info.value.code == 2  # argparse error code

    def test_parse_help_shows_usage(self) -> None:
        """--help should show usage and exit."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    def test_parse_new_help_shows_usage(self) -> None:
        """new --help should show new subcommand usage."""
        with pytest.raises(SystemExit) as exc_info:
            main(["new", "--help"])
        assert exc_info.value.code == 0


# =============================================================================
# Unit Tests: get_lan_ip
# =============================================================================


class TestGetLanIp:
    """Tests for get_lan_ip function."""

    def test_returns_string(self) -> None:
        """Returns a string IP address."""
        result = get_lan_ip()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_valid_ip_or_localhost(self) -> None:
        """Returns either a valid IP or localhost."""
        result = get_lan_ip()
        # Should be either "localhost" or an IP address
        if result != "localhost":
            parts = result.split(".")
            assert len(parts) == 4
            for part in parts:
                assert 0 <= int(part) <= 255


# =============================================================================
# Unit Tests: create_table_directly
# =============================================================================


class TestCreateTableDirectly:
    """Tests for create_table_directly function."""

    def test_creates_table_successfully(self, temp_db: Path) -> None:
        """Creates a table directly in the database."""
        result = create_table_directly(
            question="What is the best approach?",
            context="Consider performance",
            db_path=str(temp_db),
        )

        assert "id" in result
        assert result["question"] == "What is the best approach?"
        assert result["context"] == "Consider performance"
        assert result["status"] == "open"
        assert "created_at" in result
        assert "updated_at" in result

    def test_creates_table_without_context(self, temp_db: Path) -> None:
        """Creates a table without context."""
        result = create_table_directly(
            question="What is the best approach?",
            context=None,
            db_path=str(temp_db),
        )

        assert result["question"] == "What is the best approach?"
        assert result["context"] is None

    def test_creates_database_if_not_exists(self, temp_db: Path) -> None:
        """Creates the database file if it doesn't exist."""
        assert not temp_db.exists()
        create_table_directly(
            question="Test question",
            context=None,
            db_path=str(temp_db),
        )
        assert temp_db.exists()

    def test_table_persists_in_database(self, temp_db: Path) -> None:
        """Table is persisted in the database."""
        result = create_table_directly(
            question="Test question",
            context=None,
            db_path=str(temp_db),
        )

        # Verify table exists in database
        conn = sqlite3.connect(str(temp_db))
        cursor = conn.execute("SELECT id, question FROM tables WHERE id = ?", (result["id"],))
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row[0] == result["id"]
        assert row[1] == "Test question"

    def test_generates_human_readable_id(self, temp_db: Path) -> None:
        """Generates a human-readable table ID."""
        result = create_table_directly(
            question="Test question",
            context=None,
            db_path=str(temp_db),
        )

        # ID should be human-readable (words-dashes format)
        table_id = result["id"]
        assert "-" in table_id  # Should have dashes
        assert all(c.isalnum() or c == "-" for c in table_id)  # Only alphanumeric and dashes


# =============================================================================
# Unit Tests: print_startup_banner
# =============================================================================


class TestPrintStartupBanner:
    """Tests for print_startup_banner function."""

    def test_prints_table_info(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Prints table information in the banner."""
        table_data = {
            "id": "happy-whale-jogs",
            "question": "Should we use SQLAlchemy?",
            "context": None,
            "status": "open",
            "version": 1,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }

        print_startup_banner(
            table_data=table_data,
            admin_token="tk_test123",
            db_path="/path/to/tasca.db",
            port=8000,
            token_from_env=False,
        )

        output = capsys.readouterr().out

        # Check core info
        assert "happy-whale-jogs" in output
        assert "Should we use SQLAlchemy?" in output
        assert "OPEN" in output
        assert "tk_test123" in output

    def test_prints_lan_ip_mcp_url(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Prints MCP URL with LAN IP."""
        table_data = {
            "id": "test-id",
            "question": "Test?",
            "context": None,
            "status": "open",
            "version": 1,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }

        with patch("tasca.cli.get_lan_ip", return_value="192.168.1.42"):
            print_startup_banner(
                table_data=table_data,
                admin_token="tk_test",
                db_path="/path/to/tasca.db",
                port=8000,
                token_from_env=False,
            )

        output = capsys.readouterr().out
        assert "http://192.168.1.42:8000/mcp" in output

    def test_prints_stdio_mcp_config(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Prints STDIO MCP config JSON."""
        table_data = {
            "id": "test-id",
            "question": "Test?",
            "context": None,
            "status": "open",
            "version": 1,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }

        print_startup_banner(
            table_data=table_data,
            admin_token="tk_test",
            db_path="/path/to/tasca.db",
            port=8000,
            token_from_env=False,
        )

        output = capsys.readouterr().out
        # Check that JSON config is present (no ANSI codes)
        assert '"tasca"' in output
        assert '"command":"uv"' in output
        assert "tasca-mcp" in output

    def test_prints_http_mcp_config(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Prints HTTP MCP config JSON."""
        table_data = {
            "id": "test-id",
            "question": "Test?",
            "context": None,
            "status": "open",
            "version": 1,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }

        with patch("tasca.cli.get_lan_ip", return_value="192.168.1.42"):
            print_startup_banner(
                table_data=table_data,
                admin_token="tk_test",
                db_path="/path/to/tasca.db",
                port=8000,
                token_from_env=False,
            )

        output = capsys.readouterr().out
        # Check HTTP MCP config is present
        assert '"mcpServers"' in output
        assert '"url":"http://192.168.1.42:8000/mcp"' in output

    def test_token_from_env_shows_different_message(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Shows different message when token comes from env."""
        table_data = {
            "id": "test-id",
            "question": "Test?",
            "context": None,
            "status": "open",
            "version": 1,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }

        print_startup_banner(
            table_data=table_data,
            admin_token="tk_test",
            db_path="/path/to/tasca.db",
            port=8000,
            token_from_env=True,
        )

        output = capsys.readouterr().out
        # When token from env, the admin token line shows the source indicator
        assert "(from TASCA_ADMIN_TOKEN env)" in output
        # Token is still shown in both places (human needs to tell agent)
        assert "tk_test" in output

    def test_ctrlc_message_present(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Ctrl+C message is present."""
        table_data = {
            "id": "test-id",
            "question": "Test?",
            "context": None,
            "status": "open",
            "version": 1,
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
        }

        print_startup_banner(
            table_data=table_data,
            admin_token="tk_test",
            db_path="/path/to/tasca.db",
            port=8000,
            token_from_env=False,
        )

        output = capsys.readouterr().out
        assert "Ctrl+C" in output


# =============================================================================
# Unit Tests: cmd_new
# =============================================================================


class TestCmdNew:
    """Tests for cmd_new function.

    Note: cmd_new now starts a foreground uvicorn server, so we mock
    the blocking operations to test the logic.
    """

    def test_cmd_new_creates_table_and_starts_server(self) -> None:
        """cmd_new creates table and starts server in foreground."""
        args = argparse.Namespace(
            question="What is the best approach?",
            context="Consider performance",
            host=None,
            port=None,
            verbose=False,
        )

        with patch("tasca.cli.create_table_directly") as mock_create:
            with patch("tasca.cli.print_startup_banner") as mock_banner:
                with patch("uvicorn.run") as mock_uvicorn:
                    mock_create.return_value = {
                        "id": "test-table-123",
                        "question": "What is the best approach?",
                        "context": "Consider performance",
                        "status": "open",
                        "version": 1,
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                    }

                    result = cmd_new(args)

                    assert result == 0
                    mock_create.assert_called_once()
                    mock_banner.assert_called_once()
                    mock_uvicorn.assert_called_once()

    def test_cmd_new_shows_token_when_auto_generated(self) -> None:
        """cmd_new shows auto-generated token in banner."""
        args = argparse.Namespace(
            question="Test question",
            context=None,
            host=None,
            port=None,
            verbose=False,
        )

        with patch("tasca.cli.create_table_directly") as mock_create:
            with patch("tasca.cli.print_startup_banner") as mock_banner:
                with patch("uvicorn.run"):
                    with patch("tasca.config.settings") as mock_settings:
                        mock_settings.version = "0.1.0"
                        mock_settings.api_host = "0.0.0.0"
                        mock_settings.api_port = 8000
                        mock_settings.db_path = "/tmp/test.db"
                        mock_settings.admin_token = "tk_auto_generated"
                        mock_settings.admin_token_from_env = False

                        mock_create.return_value = {
                            "id": "test-id",
                            "question": "Test question",
                            "status": "open",
                        }

                        cmd_new(args)

                        # Check that banner was called with token_from_env=False
                        call_kwargs = mock_banner.call_args.kwargs
                        assert call_kwargs["token_from_env"] is False
                        assert call_kwargs["admin_token"].startswith("tk_")

    def test_cmd_new_handles_keyboard_interrupt(self) -> None:
        """cmd_new handles Ctrl+C gracefully."""
        args = argparse.Namespace(
            question="Test question",
            context=None,
            host=None,
            port=None,
            verbose=False,
        )

        with patch("tasca.cli.create_table_directly") as mock_create:
            with patch("tasca.cli.print_startup_banner"):
                with patch("uvicorn.run") as mock_uvicorn:
                    mock_create.return_value = {
                        "id": "test-id",
                        "question": "Test",
                        "status": "open",
                    }
                    mock_uvicorn.side_effect = KeyboardInterrupt()

                    result = cmd_new(args)

                    assert result == 0  # Clean exit

    def test_cmd_new_uses_custom_host_port(self) -> None:
        """cmd_new uses custom host and port."""
        args = argparse.Namespace(
            question="Test question",
            context=None,
            host="127.0.0.1",
            port=3000,
            verbose=False,
        )

        with patch("tasca.cli.create_table_directly") as mock_create:
            with patch("tasca.cli.print_startup_banner"):
                with patch("uvicorn.run") as mock_uvicorn:
                    mock_create.return_value = {
                        "id": "test-id",
                        "question": "Test",
                        "status": "open",
                    }

                    cmd_new(args)

                    # Check uvicorn.run was called with custom host/port
                    call_kwargs = mock_uvicorn.call_args.kwargs
                    assert call_kwargs["host"] == "127.0.0.1"
                    assert call_kwargs["port"] == 3000

    def test_cmd_new_create_table_error_returns_1(self) -> None:
        """cmd_new returns 1 when table creation fails."""
        args = argparse.Namespace(
            question="Test question",
            context=None,
            host=None,
            port=None,
        )

        with patch("tasca.cli.create_table_directly") as mock_create:
            mock_create.side_effect = RuntimeError("Database error")

            result = cmd_new(args)

            assert result == 1


# =============================================================================
# Unit Tests: is_server_running (legacy)
# =============================================================================


class TestIsServerRunning:
    """Tests for is_server_running function (legacy, still used)."""

    def test_server_running_returns_true(self) -> None:
        """Returns True when server responds with 200."""
        mock_response = Mock()
        mock_response.status_code = 200

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = is_server_running("http://localhost:8000")
            assert result is True
            mock_client.get.assert_called_once_with("http://localhost:8000/api/v1/health")

    def test_server_not_running_returns_false(self) -> None:
        """Returns False when connection fails."""
        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client_class.return_value = mock_client

            result = is_server_running("http://localhost:8000")
            assert result is False


# =============================================================================
# Unit Tests: create_table_via_rest (legacy)
# =============================================================================


class TestCreateTableViaRest:
    """Tests for create_table_via_rest function."""

    def test_create_table_success(self) -> None:
        """Successfully creates table via REST API."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "test-table-123",
            "question": "What is the best approach?",
            "context": "Consider performance",
            "status": "open",
        }

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = create_table_via_rest(
                question="What is the best approach?",
                context="Consider performance",
                base_url="http://localhost:8000",
                admin_token="secret-token",
            )

            assert result["id"] == "test-table-123"
            mock_client.post.assert_called_once()


# =============================================================================
# Unit Tests: create_table_via_mcp
# =============================================================================


class TestCreateTableViaMcp:
    """Tests for create_table_via_mcp function."""

    def test_create_table_via_mcp_success(self) -> None:
        """Successfully creates table via MCP stdio."""
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()

        # Mock responses: init response, tool response
        init_response = '{"jsonrpc":"2.0","id":1,"result":{}}'
        tool_response = '{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"{\\"ok\\":true,\\"data\\":{\\"id\\":\\"test-table-123\\"}}"}]}}'
        mock_process.stdout.readline.side_effect = [init_response, tool_response]

        with patch("subprocess.Popen", return_value=mock_process):
            result = create_table_via_mcp(
                question="What is the best approach?",
                context="Consider performance",
            )
            assert result.get("id") == "test-table-123" or result.get("ok") is True
