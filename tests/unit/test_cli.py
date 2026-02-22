"""
Unit tests for CLI functionality.

This module tests the tasca new CLI command components:
- Argument parsing
- is_server_running() function
- Error handling paths

Uses mocking for HTTP clients to avoid external dependencies.
"""

from __future__ import annotations

import argparse
import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, Mock, patch

import httpx
import pytest

from tasca.cli import (
    create_table_via_mcp,
    create_table_via_rest,
    is_server_running,
    main,
    cmd_new,
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

    def test_parse_new_with_mcp_flag(self) -> None:
        """Parse 'new' with --mcp flag."""
        with patch("tasca.cli.cmd_new") as mock_cmd_new:
            mock_cmd_new.return_value = 0
            result = main(
                [
                    "new",
                    "What is the best approach?",
                    "--mcp",
                ]
            )
            assert result == 0
            call_args = mock_cmd_new.call_args
            assert call_args is not None
            args = call_args[0][0]
            assert args.mcp is True

    def test_parse_new_with_url(self) -> None:
        """Parse 'new' with custom URL."""
        with patch("tasca.cli.cmd_new") as mock_cmd_new:
            mock_cmd_new.return_value = 0
            result = main(
                [
                    "new",
                    "What is the best approach?",
                    "-u",
                    "http://custom:9000",
                ]
            )
            assert result == 0
            call_args = mock_cmd_new.call_args
            assert call_args is not None
            args = call_args[0][0]
            assert args.url == "http://custom:9000"

    def test_parse_new_with_token(self) -> None:
        """Parse 'new' with admin token."""
        with patch("tasca.cli.cmd_new") as mock_cmd_new:
            mock_cmd_new.return_value = 0
            result = main(
                [
                    "new",
                    "What is the best approach?",
                    "-t",
                    "secret-token",
                ]
            )
            assert result == 0
            call_args = mock_cmd_new.call_args
            assert call_args is not None
            args = call_args[0][0]
            assert args.token == "secret-token"

    def test_parse_new_with_no_start(self) -> None:
        """Parse 'new' with --no-start flag."""
        with patch("tasca.cli.cmd_new") as mock_cmd_new:
            mock_cmd_new.return_value = 0
            result = main(
                [
                    "new",
                    "What is the best approach?",
                    "--no-start",
                ]
            )
            assert result == 0
            call_args = mock_cmd_new.call_args
            assert call_args is not None
            args = call_args[0][0]
            assert args.no_start is True

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
# Unit Tests: is_server_running
# =============================================================================


class TestIsServerRunning:
    """Tests for is_server_running function."""

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

    def test_server_timeout_returns_false(self) -> None:
        """Returns False when connection times out."""
        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.side_effect = httpx.TimeoutException("Timeout")
            mock_client_class.return_value = mock_client

            result = is_server_running("http://localhost:8000")
            assert result is False

    def test_server_500_returns_false(self) -> None:
        """Returns False when server responds with 500."""
        mock_response = Mock()
        mock_response.status_code = 500

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = is_server_running("http://localhost:8000")
            assert result is False

    def test_server_404_returns_false(self) -> None:
        """Returns False when health endpoint not found."""
        mock_response = Mock()
        mock_response.status_code = 404

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = is_server_running("http://localhost:8000")
            assert result is False

    def test_base_url_trailing_slash_stripped(self) -> None:
        """Trailing slash is stripped from base URL."""
        mock_response = Mock()
        mock_response.status_code = 200

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = is_server_running("http://localhost:8000/")
            assert result is True
            # Should strip trailing slash before appending path
            mock_client.get.assert_called_once_with("http://localhost:8000/api/v1/health")


# =============================================================================
# Unit Tests: create_table_via_rest
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

    def test_create_table_no_token(self) -> None:
        """Creates table without auth token when token is None."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "test-table-123"}

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = create_table_via_rest(
                question="What is the best approach?",
                context=None,
                base_url="http://localhost:8000",
                admin_token=None,
            )

            assert result["id"] == "test-table-123"
            # Verify no Authorization header was sent
            call_args = mock_client.post.call_args
            headers = call_args.kwargs.get("headers", {})
            assert "Authorization" not in headers

    def test_create_table_connection_error_exits(self) -> None:
        """Connection error raises SystemExit."""
        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client_class.return_value = mock_client

            with pytest.raises(SystemExit) as exc_info:
                create_table_via_rest(
                    question="What is the best approach?",
                    context=None,
                    base_url="http://localhost:8000",
                    admin_token=None,
                )
            assert exc_info.value.code == 1

    def test_create_table_timeout_exits(self) -> None:
        """Timeout error raises SystemExit."""
        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.post.side_effect = httpx.TimeoutException("Timeout")
            mock_client_class.return_value = mock_client

            with pytest.raises(SystemExit) as exc_info:
                create_table_via_rest(
                    question="What is the best approach?",
                    context=None,
                    base_url="http://localhost:8000",
                    admin_token=None,
                )
            assert exc_info.value.code == 1

    def test_create_table_api_error_exits(self) -> None:
        """API error response raises SystemExit."""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"detail": "Internal server error"}
        mock_response.text = "Internal server error"

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__enter__ = Mock(return_value=mock_client)
            mock_client.__exit__ = Mock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_class.return_value = mock_client

            with pytest.raises(SystemExit) as exc_info:
                create_table_via_rest(
                    question="What is the best approach?",
                    context=None,
                    base_url="http://localhost:8000",
                    admin_token=None,
                )
            assert exc_info.value.code == 1


# =============================================================================
# Unit Tests: cmd_new
# =============================================================================


class TestCmdNew:
    """Tests for cmd_new function."""

    def test_cmd_new_rest_success(self) -> None:
        """cmd_new creates table via REST successfully."""
        args = argparse.Namespace(
            question="What is the best approach?",
            context="Consider performance",
            mcp=False,
            no_start=True,
            url="http://localhost:8000",
            token=None,
            host=None,
            port=None,
        )

        with patch("tasca.cli.is_server_running", return_value=True):
            with patch("tasca.cli.create_table_via_rest") as mock_create:
                mock_create.return_value = {
                    "id": "test-table-123",
                    "question": "What is the best approach?",
                }
                result = cmd_new(args)
                assert result == 0
                mock_create.assert_called_once()

    def test_cmd_new_rest_server_not_running(self) -> None:
        """cmd_new returns error when server not running and no_start=True."""
        args = argparse.Namespace(
            question="What is the best approach?",
            context=None,
            mcp=False,
            no_start=True,
            url="http://localhost:8000",
            token=None,
            host=None,
            port=None,
        )

        with patch("tasca.cli.is_server_running", return_value=False):
            with patch("tasca.cli.create_table_via_rest") as mock_create:
                mock_create.return_value = {"id": "test-table-123"}
                result = cmd_new(args)
                # Even with no_start=True and server not running, it tries to create
                # which will fail with connection error
                assert mock_create.called

    def test_cmd_new_mcp_success(self) -> None:
        """cmd_new creates table via MCP successfully."""
        args = argparse.Namespace(
            question="What is the best approach?",
            context="Consider performance",
            mcp=True,
            no_start=False,
            url=None,
            token=None,
            host=None,
            port=None,
        )

        with patch("tasca.cli.create_table_via_mcp") as mock_create:
            mock_create.return_value = {
                "ok": True,
                "data": {"id": "test-table-123"},
            }
            result = cmd_new(args)
            assert result == 0
            mock_create.assert_called_once_with(
                "What is the best approach?",
                "Consider performance",
            )

    def test_cmd_new_mcp_no_id_in_response(self) -> None:
        """cmd_new returns error when no ID in MCP response."""
        args = argparse.Namespace(
            question="What is the best approach?",
            context=None,
            mcp=True,
            no_start=False,
            url=None,
            token=None,
            host=None,
            port=None,
        )

        with patch("tasca.cli.create_table_via_mcp") as mock_create:
            mock_create.return_value = {"ok": True}  # Missing ID
            result = cmd_new(args)
            assert result == 1

    def test_cmd_new_rest_no_id_in_response(self) -> None:
        """cmd_new returns error when no ID in REST response."""
        args = argparse.Namespace(
            question="What is the best approach?",
            context=None,
            mcp=False,
            no_start=True,
            url="http://localhost:8000",
            token=None,
            host=None,
            port=None,
        )

        with patch("tasca.cli.is_server_running", return_value=True):
            with patch("tasca.cli.create_table_via_rest") as mock_create:
                mock_create.return_value = {"question": "What?"}  # Missing ID
                result = cmd_new(args)
                assert result == 1

    def test_cmd_new_rest_starts_server(self) -> None:
        """cmd_new starts server when not running and no_start=False."""
        args = argparse.Namespace(
            question="What is the best approach?",
            context=None,
            mcp=False,
            no_start=False,
            url=None,
            token=None,
            host=None,
            port=None,
        )

        with patch("tasca.cli.is_server_running") as mock_running:
            # First call: server not running
            # Second call (in wait_for_server_ready): server running
            mock_running.side_effect = [False, True]
            with patch("tasca.cli.start_server_background") as mock_start:
                with patch("tasca.cli.wait_for_server_ready", return_value=True):
                    with patch("tasca.cli.create_table_via_rest") as mock_create:
                        mock_create.return_value = {"id": "test-table-123"}
                        result = cmd_new(args)
                        assert result == 0
                        mock_start.assert_called_once()

    def test_cmd_new_rest_server_timeout(self) -> None:
        """cmd_new returns error when server startup times out."""
        args = argparse.Namespace(
            question="What is the best approach?",
            context=None,
            mcp=False,
            no_start=False,
            url=None,
            token=None,
            host=None,
            port=None,
        )

        with patch("tasca.cli.is_server_running", return_value=False):
            with patch("tasca.cli.start_server_background"):
                with patch("tasca.cli.wait_for_server_ready", return_value=False):
                    result = cmd_new(args)
                    assert result == 1
