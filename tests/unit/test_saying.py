"""
Tests for saying_service core logic and saying_repo atomic sequence allocation.

Concurrency tests verify that sequence allocation is atomic and produces
no gaps or duplicates under concurrent appends.
"""

import os
import sqlite3
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pytest
from returns.result import Failure, Success

from tasca.core.domain.saying import Saying, SayingId, Speaker, SpeakerKind
from tasca.core.services.saying_service import (
    compute_next_sequence,
    generate_sequence_range,
    get_max_sequence,
    order_to_sequence,
    sequence_to_order,
    validate_sequence_is_next,
)
from tasca.shell.storage.database import apply_schema
from tasca.shell.storage.saying_repo import (
    append_saying,
    count_sayings_by_table,
    get_saying_by_id,
    get_saying_by_sequence,
    get_table_max_sequence,
    list_sayings_by_table,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def memory_db() -> sqlite3.Connection:
    """Create an in-memory database with schema applied."""
    conn = sqlite3.connect(":memory:")
    schema_result = apply_schema(conn)
    assert isinstance(schema_result, Success), f"Failed to apply schema: {schema_result}"
    return conn


# =============================================================================
# Core Service Tests
# =============================================================================


class TestComputeNextSequence:
    """Tests for compute_next_sequence pure function."""

    def test_first_saying(self) -> None:
        """First saying has sequence 0 when current_max is -1."""
        assert compute_next_sequence(-1) == 0

    def test_increment(self) -> None:
        """Sequence increments by 1."""
        assert compute_next_sequence(0) == 1
        assert compute_next_sequence(5) == 6
        assert compute_next_sequence(99) == 100

    def test_large_sequence(self) -> None:
        """Large sequence numbers work correctly."""
        assert compute_next_sequence(1000000) == 1000001

    def test_negative_rejected(self) -> None:
        """Negative input below -1 should be rejected by contract."""
        import deal

        with pytest.raises(deal.PreContractError):
            compute_next_sequence(-2)


class TestValidateSequenceIsNext:
    """Tests for validate_sequence_is_next pure function."""

    def test_valid_first_sequence(self) -> None:
        """Sequence 0 is valid when current_max is -1."""
        assert validate_sequence_is_next(0, -1) is True

    def test_valid_increment(self) -> None:
        """Sequence n+1 is valid when current_max is n."""
        assert validate_sequence_is_next(1, 0) is True
        assert validate_sequence_is_next(5, 4) is True

    def test_invalid_gap(self) -> None:
        """Sequence with gap is invalid."""
        assert validate_sequence_is_next(2, 0) is False  # Skipped 1
        assert validate_sequence_is_next(10, 5) is False  # Skipped 6-9

    def test_invalid_duplicate(self) -> None:
        """Duplicate sequence is invalid."""
        assert validate_sequence_is_next(0, 0) is False
        assert validate_sequence_is_next(5, 5) is False

    def test_invalid_out_of_order(self) -> None:
        """Sequence before current is invalid."""
        assert validate_sequence_is_next(3, 5) is False


class TestGetMaxSequence:
    """Tests for get_max_sequence pure function."""

    def test_empty_list(self) -> None:
        """Empty list returns -1."""
        assert get_max_sequence([]) == -1

    def test_single_element(self) -> None:
        """Single element list returns that element."""
        assert get_max_sequence([5]) == 5

    def test_multiple_elements(self) -> None:
        """Returns maximum of all elements."""
        assert get_max_sequence([1, 2, 5, 3]) == 5
        assert get_max_sequence([10, 2, 8, 1, 9]) == 10

    def test_unsorted(self) -> None:
        """Works with unsorted lists."""
        assert get_max_sequence([5, 1, 9, 3]) == 9


class TestGenerateSequenceRange:
    """Tests for generate_sequence_range pure function."""

    def test_empty_range(self) -> None:
        """Zero count returns empty list."""
        assert generate_sequence_range(0, 0) == []
        assert generate_sequence_range(10, 0) == []

    def test_single_element(self) -> None:
        """Count of 1 returns single element."""
        assert generate_sequence_range(0, 1) == [0]
        assert generate_sequence_range(10, 1) == [10]

    def test_range(self) -> None:
        """Generates correct range."""
        assert generate_sequence_range(0, 3) == [0, 1, 2]
        assert generate_sequence_range(5, 4) == [5, 6, 7, 8]


class TestSequenceOrderConversion:
    """Tests for sequence<->order conversions."""

    def test_sequence_to_order(self) -> None:
        """Zero-indexed to one-indexed."""
        assert sequence_to_order(0) == 1
        assert sequence_to_order(1) == 2
        assert sequence_to_order(99) == 100

    def test_order_to_sequence(self) -> None:
        """One-indexed to zero-indexed."""
        assert order_to_sequence(1) == 0
        assert order_to_sequence(2) == 1
        assert order_to_sequence(100) == 99

    def test_roundtrip(self) -> None:
        """Conversion is reversible."""
        for seq in [0, 1, 50, 100]:
            assert order_to_sequence(sequence_to_order(seq)) == seq
            assert sequence_to_order(order_to_sequence(seq + 1)) == seq + 1


# =============================================================================
# Repository Tests
# =============================================================================


class TestAppendSaying:
    """Tests for atomic append_saying operation."""

    def test_append_first_saying(self, memory_db: sqlite3.Connection) -> None:
        """First saying has sequence 0."""
        table_id = str(uuid.uuid4())
        speaker = Speaker(kind=SpeakerKind.AGENT, name="TestAgent")

        result = append_saying(memory_db, table_id, speaker, "Hello, world!")

        assert isinstance(result, Success)
        saying = result.unwrap()
        assert saying.sequence == 0
        assert saying.table_id == table_id
        assert saying.content == "Hello, world!"
        assert saying.speaker.kind == SpeakerKind.AGENT
        assert saying.speaker.name == "TestAgent"

    def test_append_multiple_sayings(self, memory_db: sqlite3.Connection) -> None:
        """Multiple sayings get sequential sequences."""
        table_id = str(uuid.uuid4())
        speaker = Speaker(kind=SpeakerKind.AGENT, name="TestAgent")

        for i in range(5):
            result = append_saying(memory_db, table_id, speaker, f"Message {i}")
            assert isinstance(result, Success)
            saying = result.unwrap()
            assert saying.sequence == i

    def test_append_different_tables(self, memory_db: sqlite3.Connection) -> None:
        """Each table has its own sequence."""
        table_id_1 = str(uuid.uuid4())
        table_id_2 = str(uuid.uuid4())
        speaker = Speaker(kind=SpeakerKind.AGENT, name="TestAgent")

        # Append to table 1
        result1 = append_saying(memory_db, table_id_1, speaker, "Table 1, Msg 1")
        assert isinstance(result1, Success)
        assert result1.unwrap().sequence == 0

        # Append to table 2
        result2 = append_saying(memory_db, table_id_2, speaker, "Table 2, Msg 1")
        assert isinstance(result2, Success)
        assert result2.unwrap().sequence == 0

        # Append to table 1 again
        result3 = append_saying(memory_db, table_id_1, speaker, "Table 1, Msg 2")
        assert isinstance(result3, Success)
        assert result3.unwrap().sequence == 1

    def test_append_sets_created_at(self, memory_db: sqlite3.Connection) -> None:
        """created_at is set to current time."""
        table_id = str(uuid.uuid4())
        speaker = Speaker(kind=SpeakerKind.AGENT, name="TestAgent")
        before = datetime.now(timezone.utc)

        result = append_saying(memory_db, table_id, speaker, "Test")

        after = datetime.now(timezone.utc)
        assert isinstance(result, Success)
        saying = result.unwrap()
        assert before <= saying.created_at <= after


class TestGetSayingById:
    """Tests for get_saying_by_id."""

    def test_get_existing_saying(self, memory_db: sqlite3.Connection) -> None:
        """Can retrieve saying by ID."""
        table_id = str(uuid.uuid4())
        speaker = Speaker(kind=SpeakerKind.AGENT, name="TestAgent")

        append_result = append_saying(memory_db, table_id, speaker, "Test message")
        assert isinstance(append_result, Success)
        original = append_result.unwrap()

        get_result = get_saying_by_id(memory_db, original.id)
        assert isinstance(get_result, Success)
        found = get_result.unwrap()
        assert found is not None
        assert found.id == original.id
        assert found.sequence == original.sequence
        assert found.content == original.content

    def test_get_nonexistent_saying(self, memory_db: sqlite3.Connection) -> None:
        """Returns None for nonexistent ID."""
        result = get_saying_by_id(memory_db, str(uuid.uuid4()))
        assert isinstance(result, Success)
        assert result.unwrap() is None


class TestGetSayingBySequence:
    """Tests for get_saying_by_sequence."""

    def test_get_by_sequence(self, memory_db: sqlite3.Connection) -> None:
        """Can retrieve saying by table_id and sequence."""
        table_id = str(uuid.uuid4())
        speaker = Speaker(kind=SpeakerKind.AGENT, name="TestAgent")

        for i in range(3):
            append_saying(memory_db, table_id, speaker, f"Message {i}")

        result = get_saying_by_sequence(memory_db, table_id, 1)
        assert isinstance(result, Success)
        saying = result.unwrap()
        assert saying is not None
        assert saying.sequence == 1
        assert saying.content == "Message 1"

    def test_get_nonexistent_sequence(self, memory_db: sqlite3.Connection) -> None:
        """Returns None for nonexistent sequence."""
        result = get_saying_by_sequence(memory_db, str(uuid.uuid4()), 999)
        assert isinstance(result, Success)
        assert result.unwrap() is None


class TestListSayingsByTable:
    """Tests for list_sayings_by_table."""

    def test_list_all_sayings(self, memory_db: sqlite3.Connection) -> None:
        """Can list all sayings for a table."""
        table_id = str(uuid.uuid4())
        speaker = Speaker(kind=SpeakerKind.AGENT, name="TestAgent")

        for i in range(5):
            append_saying(memory_db, table_id, speaker, f"Message {i}")

        result = list_sayings_by_table(memory_db, table_id)
        assert isinstance(result, Success)
        sayings = result.unwrap()
        assert len(sayings) == 5
        assert [s.sequence for s in sayings] == [0, 1, 2, 3, 4]

    def test_list_with_since_sequence(self, memory_db: sqlite3.Connection) -> None:
        """Can list sayings after a sequence."""
        table_id = str(uuid.uuid4())
        speaker = Speaker(kind=SpeakerKind.AGENT, name="TestAgent")

        for i in range(5):
            append_saying(memory_db, table_id, speaker, f"Message {i}")

        result = list_sayings_by_table(memory_db, table_id, since_sequence=2)
        assert isinstance(result, Success)
        sayings = result.unwrap()
        assert len(sayings) == 2
        assert [s.sequence for s in sayings] == [3, 4]

    def test_list_with_limit(self, memory_db: sqlite3.Connection) -> None:
        """Can limit number of sayings returned."""
        table_id = str(uuid.uuid4())
        speaker = Speaker(kind=SpeakerKind.AGENT, name="TestAgent")

        for i in range(10):
            append_saying(memory_db, table_id, speaker, f"Message {i}")

        result = list_sayings_by_table(memory_db, table_id, limit=3)
        assert isinstance(result, Success)
        sayings = result.unwrap()
        assert len(sayings) == 3
        assert [s.sequence for s in sayings] == [0, 1, 2]

    def test_list_empty_table(self, memory_db: sqlite3.Connection) -> None:
        """Empty table returns empty list."""
        result = list_sayings_by_table(memory_db, str(uuid.uuid4()))
        assert isinstance(result, Success)
        assert result.unwrap() == []


class TestGetTableMaxSequence:
    """Tests for get_table_max_sequence."""

    def test_empty_table(self, memory_db: sqlite3.Connection) -> None:
        """Empty table returns -1."""
        result = get_table_max_sequence(memory_db, str(uuid.uuid4()))
        assert isinstance(result, Success)
        assert result.unwrap() == -1

    def test_with_sayings(self, memory_db: sqlite3.Connection) -> None:
        """Returns max sequence."""
        table_id = str(uuid.uuid4())
        speaker = Speaker(kind=SpeakerKind.AGENT, name="TestAgent")

        for i in range(5):
            append_saying(memory_db, table_id, speaker, f"Message {i}")

        result = get_table_max_sequence(memory_db, table_id)
        assert isinstance(result, Success)
        assert result.unwrap() == 4


class TestCountSayingsByTable:
    """Tests for count_sayings_by_table."""

    def test_empty_table(self, memory_db: sqlite3.Connection) -> None:
        """Empty table returns 0."""
        result = count_sayings_by_table(memory_db, str(uuid.uuid4()))
        assert isinstance(result, Success)
        assert result.unwrap() == 0

    def test_with_sayings(self, memory_db: sqlite3.Connection) -> None:
        """Returns correct count."""
        table_id = str(uuid.uuid4())
        speaker = Speaker(kind=SpeakerKind.AGENT, name="TestAgent")

        for i in range(5):
            append_saying(memory_db, table_id, speaker, f"Message {i}")

        result = count_sayings_by_table(memory_db, table_id)
        assert isinstance(result, Success)
        assert result.unwrap() == 5


# =============================================================================
# Concurrency Tests
# =============================================================================


class TestConcurrencyAtomicSequenceAllocation:
    """
    Concurrency tests for atomic sequence allocation.

    These tests verify that under concurrent appends:
    1. No sequence gaps occur
    2. No duplicate sequences occur
    3. All sayings are persisted
    """

    def test_concurrent_appends_single_thread_pool(self) -> None:
        """
        Verify atomic sequence allocation with concurrent appends.

        This test uses a temp file database with WAL mode to test true
        multi-threaded concurrent access.
        """
        table_id = str(uuid.uuid4())
        speaker = Speaker(kind=SpeakerKind.AGENT, name="TestAgent")
        num_threads = 10
        sayings_per_thread = 10
        total_expected = num_threads * sayings_per_thread

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        results: list = []
        lock = threading.Lock()
        errors: list = []

        try:
            # Initialize schema with WAL mode
            init_conn = sqlite3.connect(db_path)
            init_conn.execute("PRAGMA journal_mode=WAL")
            apply_schema(init_conn)
            init_conn.close()

            def append_sayings(thread_id: int) -> int:
                """Append sayings from a single thread with its own connection."""
                thread_conn = sqlite3.connect(db_path)
                thread_conn.execute("PRAGMA busy_timeout=5000")
                success_count = 0
                try:
                    for i in range(sayings_per_thread):
                        result = append_saying(
                            thread_conn, table_id, speaker, f"Thread {thread_id}, Msg {i}"
                        )
                        with lock:
                            results.append(result)
                        if isinstance(result, Success):
                            success_count += 1
                except Exception as e:
                    with lock:
                        errors.append(str(e))
                finally:
                    thread_conn.close()
                return success_count

            # Run concurrent appends
            with ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = [
                    executor.submit(append_sayings, thread_id) for thread_id in range(num_threads)
                ]
                thread_counts = [f.result() for f in as_completed(futures)]

            # Check for unexpected errors
            if errors:
                pytest.fail(f"Thread errors: {errors}")

            # Verify all sayings were persisted
            verify_conn = sqlite3.connect(db_path)
            count_result = count_sayings_by_table(verify_conn, table_id)
            assert isinstance(count_result, Success)
            actual_count = count_result.unwrap()
            verify_conn.close()

            assert actual_count == total_expected, (
                f"Expected {total_expected} sayings, got {actual_count}"
            )

            # Verify no gaps or duplicates
            verify_conn = sqlite3.connect(db_path)
            list_result = list_sayings_by_table(verify_conn, table_id, limit=total_expected + 10)
            assert isinstance(list_result, Success)
            sayings = list_result.unwrap()
            verify_conn.close()

            sequences = sorted([s.sequence for s in sayings])
            expected_sequences = list(range(total_expected))

            assert sequences == expected_sequences, (
                f"Sequence gaps or duplicates detected. "
                f"Expected {expected_sequences[:5]}...{expected_sequences[-5:]}, "
                f"got {sequences[:5]}...{sequences[-5:]}"
            )

            # Verify (table_id, sequence) uniqueness
            unique_tuples = {(s.table_id, s.sequence) for s in sayings}
            assert len(unique_tuples) == len(sayings), (
                f"Duplicate (table_id, sequence) tuples found: "
                f"{len(unique_tuples)} unique vs {len(sayings)} total"
            )

        finally:
            os.unlink(db_path)

    def test_concurrent_appends_multiple_tables(self) -> None:
        """
        Verify sequence isolation across tables.

        Concurrent appends to different tables should have independent sequences.
        """
        num_tables = 5
        sayings_per_table = 3

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            # Initialize schema with WAL mode
            init_conn = sqlite3.connect(db_path)
            init_conn.execute("PRAGMA journal_mode=WAL")
            apply_schema(init_conn)
            init_conn.close()

            tables = [(str(uuid.uuid4()), f"Table-{i}") for i in range(num_tables)]
            speaker = Speaker(kind=SpeakerKind.AGENT, name="TestAgent")

            def append_to_table(table_id: str, table_name: str) -> tuple[str, list[int]]:
                """Append sayings to a single table and return sequences."""
                thread_conn = sqlite3.connect(db_path)
                thread_conn.execute("PRAGMA busy_timeout=5000")
                sequences = []
                try:
                    for i in range(sayings_per_table):
                        result = append_saying(
                            thread_conn, table_id, speaker, f"{table_name}, Msg {i}"
                        )
                        if isinstance(result, Success):
                            sequences.append(result.unwrap().sequence)
                finally:
                    thread_conn.close()
                return table_id, sorted(sequences)

            # Run concurrent appends to different tables
            with ThreadPoolExecutor(max_workers=num_tables) as executor:
                futures = [
                    executor.submit(append_to_table, table_id, table_name)
                    for table_id, table_name in tables
                ]
                results = [f.result() for f in as_completed(futures)]

            # Verify each table has correct independent sequences
            for table_id, sequences in results:
                assert sequences == list(range(sayings_per_table)), (
                    f"Table {table_id} sequences incorrect: {sequences}"
                )

        finally:
            os.unlink(db_path)

    def test_high_contention_appends(self) -> None:
        """
        Stress test with high contention.

        Many threads appending to the same table rapidly.
        """
        table_id = str(uuid.uuid4())
        speaker = Speaker(kind=SpeakerKind.AGENT, name="TestAgent")
        num_threads = 20
        sayings_per_thread = 5
        total_expected = num_threads * sayings_per_thread

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            # Initialize schema with WAL mode
            init_conn = sqlite3.connect(db_path)
            init_conn.execute("PRAGMA journal_mode=WAL")
            apply_schema(init_conn)
            init_conn.close()

            def append_one() -> bool:
                """Append a single saying."""
                thread_conn = sqlite3.connect(db_path)
                thread_conn.execute("PRAGMA busy_timeout=5000")
                try:
                    result = append_saying(thread_conn, table_id, speaker, "Stress test")
                    return isinstance(result, Success)
                finally:
                    thread_conn.close()

            # Create many concurrent tasks
            with ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = [executor.submit(append_one) for _ in range(total_expected)]
                results = [f.result() for f in as_completed(futures)]

            # Verify all succeeded
            success_count = sum(1 for r in results if r)
            assert success_count == total_expected, (
                f"Only {success_count}/{total_expected} appends succeeded"
            )

            # Verify no gaps
            verify_conn = sqlite3.connect(db_path)
            list_result = list_sayings_by_table(verify_conn, table_id, limit=total_expected + 10)
            assert isinstance(list_result, Success)
            sayings = list_result.unwrap()
            sequences = sorted([s.sequence for s in sayings])
            verify_conn.close()

            expected = list(range(total_expected))

            assert sequences == expected, (
                f"Gaps detected in high contention test. "
                f"Missing: {set(expected) - set(sequences)}, "
                f"Extra: {set(sequences) - set(expected)}"
            )

        finally:
            os.unlink(db_path)


# =============================================================================
# Database Schema Verification
# =============================================================================


class TestSchemaUniqueness:
    """Tests for database schema constraints."""

    def test_unique_constraint_exists(self, memory_db: sqlite3.Connection) -> None:
        """Verify UNIQUE(table_id, sequence) constraint exists."""
        # Get table info
        cursor = memory_db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='sayings'"
        )
        row = cursor.fetchone()
        assert row is not None
        create_sql = row[0]

        # Verify UNIQUE constraint
        assert "UNIQUE(table_id, sequence)" in create_sql, (
            f"UNIQUE constraint missing in schema: {create_sql}"
        )

    def test_unique_constraint_enforced(self, memory_db: sqlite3.Connection) -> None:
        """Verify UNIQUE constraint prevents duplicate sequences."""
        table_id = str(uuid.uuid4())
        speaker = Speaker(kind=SpeakerKind.AGENT, name="TestAgent")

        # Append first saying
        result1 = append_saying(memory_db, table_id, speaker, "First")
        assert isinstance(result1, Success)
        saying1 = result1.unwrap()

        # Try to manually insert duplicate (table_id, sequence)
        with pytest.raises(sqlite3.IntegrityError):
            memory_db.execute(
                """
                INSERT INTO sayings (
                    id, table_id, sequence, speaker_kind, speaker_name,
                    speaker_id, content, pinned, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),  # different ID
                    table_id,
                    saying1.sequence,  # same sequence!
                    "agent",
                    "Test",
                    None,
                    "Duplicate sequence",
                    0,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
