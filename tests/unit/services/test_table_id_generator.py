"""
Unit tests for table_id_generator shell service.

Tests verify that generate_table_id() correctly handles:
- Happy path (no collision)
- Collision retry (first collision, second succeeds)
- Max retries exceeded (all 11 attempts collide)
- DB error propagation

Uses mocked DB dependencies - no real database connections.
"""

from unittest.mock import MagicMock, patch

import pytest
from returns.result import Failure, Success

from tasca.core.domain.table import TableId
from tasca.shell.services.table_id_generator import (
    MAX_ID_RETRIES,
    TableIdGenerationError,
    generate_table_id,
)
from tasca.shell.storage.table_repo import TableNotFoundError


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_conn() -> MagicMock:
    """Create a mock database connection."""
    return MagicMock()


# =============================================================================
# Test Cases
# =============================================================================


class TestGenerateTableId:
    """Tests for generate_table_id function."""

    def test_happy_path_returns_success_when_no_collision(self, mock_conn: MagicMock) -> None:
        """generate_table_id() returns Success(id) when no collision."""
        # Arrange: Mock ID generator to return deterministic ID
        # Mock _check_id_exists to return that ID is available
        with (
            patch(
                "tasca.shell.services.table_id_generator.generate_human_readable_id",
                return_value="test-id-works",
            ),
            patch(
                "tasca.shell.services.table_id_generator._check_id_exists",
                side_effect=lambda conn, id: Success(False),  # Always available
            ),
        ):
            # Act
            result = generate_table_id(mock_conn)

            # Assert
            assert isinstance(result, Success)
            table_id = result.unwrap()
            assert isinstance(table_id, str)  # TableId is NewType(str)
            assert table_id == "test-id-works"

    def test_collision_retry_succeeds_on_second_attempt(self, mock_conn: MagicMock) -> None:
        """First call collision, second call succeeds.

        - First attempt: ID exists (collision)
        - Retry with suffix: new ID available
        """
        # Arrange: First check returns collision, second returns available
        call_count = 0

        def mock_check_with_collision(conn, table_id: str):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return Success(True)  # First ID collides
            return Success(False)  # Retry succeeds

        generated_ids = ["first-id-collides", "second-id-works"]
        generate_count = 0

        def mock_generate_id(random_choice, separator="-", suffix=None):
            nonlocal generate_count
            generate_count += 1
            if generate_count == 1:
                return generated_ids[0]
            return generated_ids[1]

        with (
            patch(
                "tasca.shell.services.table_id_generator.generate_human_readable_id",
                side_effect=mock_generate_id,
            ),
            patch(
                "tasca.shell.services.table_id_generator._check_id_exists",
                side_effect=mock_check_with_collision,
            ),
        ):
            # Act
            result = generate_table_id(mock_conn)

            # Assert
            assert isinstance(result, Success)
            table_id = result.unwrap()
            assert table_id == "second-id-works"

    def test_max_retries_exceeded_returns_failure(self, mock_conn: MagicMock) -> None:
        """All 11 attempts collide → returns Failure.

        Total attempts = 1 initial + 10 retries = 11 attempts.
        """
        # Arrange: Always return collision
        with (
            patch(
                "tasca.shell.services.table_id_generator.generate_human_readable_id",
                return_value="always-collides",
            ),
            patch(
                "tasca.shell.services.table_id_generator._check_id_exists",
                side_effect=lambda conn, id: Success(True),  # Always collides
            ),
        ):
            # Act
            result = generate_table_id(mock_conn)

            # Assert
            assert isinstance(result, Failure)
            error = result.failure()
            assert isinstance(error, TableIdGenerationError)
            # MAX_ID_RETRIES = 10, so total attempts = 10 + 1 = 11
            assert error.attempts == MAX_ID_RETRIES + 1

    def test_db_error_propagation(self, mock_conn: MagicMock) -> None:
        """DB error from get_table propagates correctly.

        When _check_id_exists returns a Failure, generate_table_id should
        propagate the error.
        """
        # Arrange: Mock _check_id_exists to return DB error
        with (
            patch(
                "tasca.shell.services.table_id_generator.generate_human_readable_id",
                return_value="test-id-error",
            ),
            patch(
                "tasca.shell.services.table_id_generator._check_id_exists",
                side_effect=lambda conn, id: Failure("Database error"),
            ),
        ):
            # Act
            result = generate_table_id(mock_conn)

            # Assert
            assert isinstance(result, Failure)
            error = result.failure()
            assert isinstance(error, TableIdGenerationError)
            # DB error on first attempt
            assert error.attempts == 1

    def test_db_error_during_retry_propagates(self, mock_conn: MagicMock) -> None:
        """DB error during retry iteration propagates with correct attempt count."""
        # Arrange: First check collides, second has DB error
        call_count = 0

        def mock_check_with_db_error(conn, table_id: str):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return Success(True)  # Collision
            return Failure("Database error during retry")

        with (
            patch(
                "tasca.shell.services.table_id_generator.generate_human_readable_id",
                return_value="test-id-retry",
            ),
            patch(
                "tasca.shell.services.table_id_generator._check_id_exists",
                side_effect=mock_check_with_db_error,
            ),
        ):
            # Act
            result = generate_table_id(mock_conn)

            # Assert
            assert isinstance(result, Failure)
            error = result.failure()
            assert isinstance(error, TableIdGenerationError)
            # DB error on second attempt (after collision)
            assert error.attempts == 2


class TestTableIdGenerationError:
    """Tests for TableIdGenerationError dataclass."""

    def test_error_message_includes_attempts(self) -> None:
        """Error message includes the number of attempts."""
        error = TableIdGenerationError(attempts=5)
        assert "5 attempts" in str(error)
        assert error.attempts == 5

    def test_error_message_max_attempts(self) -> None:
        """Error message works for max attempts."""
        error = TableIdGenerationError(attempts=MAX_ID_RETRIES + 1)
        assert f"{MAX_ID_RETRIES + 1} attempts" in str(error)
        assert error.attempts == MAX_ID_RETRIES + 1
