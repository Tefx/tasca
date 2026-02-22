"""
Unit tests for human_readable_ids module.

Tests cover:
1. generate_human_readable_id() - ID generation
2. format_human_readable_id() - Formatting
3. is_human_readable_id() - Validation
4. parse_human_readable_id() - Parsing
5. Collision handling simulation
"""

from typing import Callable

import pytest

from tasca.core.human_readable_ids import (
    ADJECTIVES,
    NOUNS,
    VERBS,
    pick_random_word,
    format_human_readable_id,
    generate_human_readable_id,
    is_human_readable_id,
    is_valid_word,
    parse_human_readable_id,
    calculate_total_combinations,
    get_unique_word_count,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def deterministic_first() -> Callable[[list[str]], str]:
    """Returns a function that always picks the first element."""
    return lambda lst: lst[0]


@pytest.fixture
def deterministic_last() -> Callable[[list[str]], str]:
    """Returns a function that always picks the last element."""
    return lambda lst: lst[-1]


@pytest.fixture
def deterministic_index_factory() -> Callable[[int], Callable[[list[str]], str]]:
    """Factory for creating a deterministic picker at a specific index."""

    def _factory(index: int) -> Callable[[list[str]], str]:
        return lambda lst: lst[index]

    return _factory


# =============================================================================
# Tests: pick_random_word
# =============================================================================


class TestPickRandomWord:
    """Tests for pick_random_word function."""

    def test_picks_first_element(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """Should pick the first element when random_choice returns index 0."""
        words = ["apple", "banana", "cherry"]
        result = pick_random_word(words, deterministic_first)
        assert result == "apple"

    def test_picks_last_element(self, deterministic_last: Callable[[list[str]], str]) -> None:
        """Should pick the last element when random_choice returns last index."""
        words = ["apple", "banana", "cherry"]
        result = pick_random_word(words, deterministic_last)
        assert result == "cherry"

    def test_works_with_tuple(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """Should work with tuple input."""
        words = ("one", "two", "three")
        result = pick_random_word(words, deterministic_first)
        assert result == "one"

    def test_works_with_word_list_constants(
        self, deterministic_first: Callable[[list[str]], str]
    ) -> None:
        """Should work with the module's word list constants."""
        result = pick_random_word(ADJECTIVES, deterministic_first)
        assert result == ADJECTIVES[0]
        assert result == "able"

    def test_single_element_list(self) -> None:
        """Should work with a single-element list."""
        words = ["only"]
        result = pick_random_word(words, lambda lst: lst[0])
        assert result == "only"

    def test_returns_string_from_list(
        self, deterministic_first: Callable[[list[str]], str]
    ) -> None:
        """Should return a string from the provided list."""
        words = ["x", "y", "z"]
        result = pick_random_word(words, deterministic_first)
        assert result in words

    def test_different_indices_give_different_results(
        self, deterministic_index_factory: Callable[[int], Callable[[list[str]], str]]
    ) -> None:
        """Different indices should give different results."""
        words = ["first", "second", "third"]
        pick_0 = deterministic_index_factory(0)
        pick_1 = deterministic_index_factory(1)

        assert pick_random_word(words, pick_0) == "first"
        assert pick_random_word(words, pick_1) == "second"


# =============================================================================
# Tests: format_human_readable_id
# =============================================================================


class TestFormatHumanReadableId:
    """Tests for format_human_readable_id function."""

    def test_basic_format(self) -> None:
        """Should format three words with default separator."""
        result = format_human_readable_id("quick", "fox", "jumps")
        assert result == "quick-fox-jumps"

    def test_with_suffix(self) -> None:
        """Should append numeric suffix."""
        result = format_human_readable_id("brave", "panda", "dances", suffix=42)
        assert result == "brave-panda-dances-42"

    def test_custom_separator(self) -> None:
        """Should use custom separator."""
        result = format_human_readable_id("clever", "owl", "flies", separator="_")
        assert result == "clever_owl_flies"

    def test_custom_separator_with_suffix(self) -> None:
        """Should use custom separator with suffix."""
        result = format_human_readable_id("wise", "wolf", "runs", separator="_", suffix=99)
        assert result == "wise_wolf_runs_99"

    def test_suffix_zero(self) -> None:
        """Should handle suffix=0."""
        result = format_human_readable_id("calm", "cat", "sleeps", suffix=0)
        assert result == "calm-cat-sleeps-0"

    def test_large_suffix(self) -> None:
        """Should handle large suffix values."""
        result = format_human_readable_id("happy", "dog", "plays", suffix=999999)
        assert result == "happy-dog-plays-999999"

    def test_separator_underscore(self) -> None:
        """Should work with underscore separator."""
        result = format_human_readable_id("kind", "bear", "walks", separator="_")
        assert result == "kind_bear_walks"

    def test_separator_dot(self) -> None:
        """Should work with dot separator."""
        result = format_human_readable_id("gentle", "deer", "grazes", separator=".")
        assert result == "gentle.deer.grazes"

    def test_suffix_none_explicitly(self) -> None:
        """Should handle explicitly passing None for suffix."""
        result = format_human_readable_id("sharp", "hawk", "soars", suffix=None)
        assert result == "sharp-hawk-soars"

    def test_returns_string(self) -> None:
        """Should always return a string."""
        result = format_human_readable_id("a", "b", "c")
        assert isinstance(result, str)

    def test_has_three_parts_without_suffix(self) -> None:
        """Result should have exactly 3 parts without suffix."""
        result = format_human_readable_id("swift", "tiger", "hunts")
        parts = result.split("-")
        assert len(parts) == 3

    def test_has_four_parts_with_suffix(self) -> None:
        """Result should have exactly 4 parts with suffix."""
        result = format_human_readable_id("tall", "giraffe", "eats", suffix=7)
        parts = result.split("-")
        assert len(parts) == 4

    def test_parts_are_in_correct_order(self) -> None:
        """Parts should be in adjective-noun-verb order."""
        result = format_human_readable_id("ADJ", "NOUN", "VERB")
        parts = result.split("-")
        assert parts == ["ADJ", "NOUN", "VERB"]


# =============================================================================
# Tests: generate_human_readable_id
# =============================================================================


class TestGenerateHumanReadableId:
    """Tests for generate_human_readable_id function."""

    def test_basic_generation(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """Should generate ID with first word from each list."""
        result = generate_human_readable_id(deterministic_first)
        assert result == "able-ant-dances"

    def test_generation_with_suffix(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """Should include suffix in generated ID."""
        result = generate_human_readable_id(deterministic_first, suffix=1)
        assert result == "able-ant-dances-1"

    def test_custom_separator(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """Should use custom separator."""
        result = generate_human_readable_id(deterministic_first, separator="_")
        assert result == "able_ant_dances"

    def test_last_words(self, deterministic_last: Callable[[list[str]], str]) -> None:
        """Should use last word from each list."""
        result = generate_human_readable_id(deterministic_last)
        assert result == "witty-wind-waves"

    def test_last_words_with_suffix(self, deterministic_last: Callable[[list[str]], str]) -> None:
        """Should use last words with suffix."""
        result = generate_human_readable_id(deterministic_last, suffix=999)
        assert result == "witty-wind-waves-999"

    def test_returns_string(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """Should return a string."""
        result = generate_human_readable_id(deterministic_first)
        assert isinstance(result, str)

    def test_format_matches_is_human_readable_id(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """Generated ID should pass validation."""
        result = generate_human_readable_id(deterministic_first)
        assert is_human_readable_id(result)

    def test_format_with_suffix_passes_validation(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """Generated ID with suffix should pass validation."""
        result = generate_human_readable_id(deterministic_first, suffix=42)
        assert is_human_readable_id(result)

    def test_random_choice_receives_list(self) -> None:
        """random_choice should receive a list of strings."""
        received = []

        def capture_choice(lst):
            received.append(type(lst))
            received.append(len(lst))
            return lst[0]

        generate_human_readable_id(capture_choice)

        assert list in received
        assert any(isinstance(x, int) and x > 0 for x in received)

    def test_different_random_choices_give_different_ids(self) -> None:
        """Different random_choice functions should give different IDs."""
        first_picker = lambda lst: lst[0]
        last_picker = lambda lst: lst[-1]

        id1 = generate_human_readable_id(first_picker)
        id2 = generate_human_readable_id(last_picker)

        assert id1 != id2

    def test_words_come_from_word_lists(self, deterministic_index_factory: Callable[[int], Callable[[list[str]], str]]) -> None:
        """Generated words should come from the defined word lists."""
        pick_0 = deterministic_index_factory(0)
        pick_1 = deterministic_index_factory(1)

        result = generate_human_readable_id(pick_0)
        adjective, noun, verb, _ = parse_human_readable_id(result)

        assert adjective in ADJECTIVES
        assert noun in NOUNS
        assert verb in VERBS

        # Now with different indices
        result2 = generate_human_readable_id(pick_1)
        adjective2, noun2, verb2, _ = parse_human_readable_id(result2)

        assert adjective2 in ADJECTIVES
        assert noun2 in NOUNS
        assert verb2 in VERBS

    def test_suffix_appears_at_end(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """Suffix should appear at the end of the ID."""
        result = generate_human_readable_id(deterministic_first, suffix=12345)
        assert result.endswith("-12345")


# =============================================================================
# Tests: is_human_readable_id
# =============================================================================


class TestIsHumanReadableId:
    """Tests for is_human_readable_id function."""

    # --- Valid IDs ---

    def test_valid_three_part_id(self) -> None:
        """Valid three-part ID should return True."""
        assert is_human_readable_id("quick-fox-jumps") is True

    def test_valid_four_part_id_with_suffix(self) -> None:
        """Valid four-part ID with numeric suffix should return True."""
        assert is_human_readable_id("quick-fox-jumps-123") is True

    def test_custom_separator_valid(self) -> None:
        """Valid ID with custom separator should return True."""
        assert is_human_readable_id("quick_fox_jumps", separator="_") is True

    def test_custom_separator_with_suffix_valid(self) -> None:
        """Valid ID with custom separator and suffix should return True."""
        assert is_human_readable_id("quick_fox_jumps_456", separator="_") is True

    def test_suffix_zero_valid(self) -> None:
        """Suffix 0 should be valid."""
        assert is_human_readable_id("brave-panda-dances-0") is True

    def test_single_digit_suffix(self) -> None:
        """Single digit suffix should be valid."""
        assert is_human_readable_id("calm-cat-sleeps-5") is True

    def test_long_numeric_suffix(self) -> None:
        """Long numeric suffix should be valid."""
        assert is_human_readable_id("happy-dog-plays-9999999") is True

    def test_validates_format_not_words(self) -> None:
        """Should validate format, not whether words are from lists."""
        # These words aren't in our word lists but format is correct
        assert is_human_readable_id("foo-bar-baz") is True
        assert is_human_readable_id("xyz-abc-qwerty-999") is True

    # --- Invalid IDs: Structure ---

    def test_empty_string_invalid(self) -> None:
        """Empty string should return False."""
        assert is_human_readable_id("") is False

    def test_two_parts_invalid(self) -> None:
        """Two parts should return False."""
        assert is_human_readable_id("just-two") is False

    def test_five_parts_invalid(self) -> None:
        """Five parts should return False."""
        assert is_human_readable_id("too-many-parts-here-now-ok") is False

    def test_single_word_invalid(self) -> None:
        """Single word should return False."""
        assert is_human_readable_id("single") is False

    # --- Invalid IDs: Characters ---

    def test_uppercase_invalid(self) -> None:
        """Uppercase letters should return False."""
        assert is_human_readable_id("Quick-fox-jumps") is False

    def test_mixed_case_invalid(self) -> None:
        """Mixed case should return False."""
        assert is_human_readable_id("Quick-Fox-Jumps") is False

    def test_numbers_in_word_valid(self) -> None:
        """Numbers in word parts should be valid (alphanumeric is allowed)."""
        # The implementation uses .isalnum() which allows numbers in words
        assert is_human_readable_id("qu1ck-f0x-jump5") is True

    def test_special_chars_invalid(self) -> None:
        """Special characters should return False."""
        assert is_human_readable_id("quick-fox-jumps!") is False
        assert is_human_readable_id("quick@fox-jumps") is False

    def test_spaces_invalid(self) -> None:
        """Spaces should return False."""
        assert is_human_readable_id("quick fox jumps") is False

    def test_tabs_invalid(self) -> None:
        """Tabs should return False."""
        assert is_human_readable_id("quick\tfox\tjumps") is False

    def test_whitespace_in_id_invalid(self) -> None:
        """Any whitespace should return False."""
        assert is_human_readable_id("quick-fox jumps") is False

    # --- Invalid IDs: Suffix ---

    def test_non_numeric_suffix_invalid(self) -> None:
        """Non-numeric suffix should return False."""
        assert is_human_readable_id("quick-fox-jumps-abc") is False

    def test_mixed_suffix_invalid(self) -> None:
        """Mixed alphanumeric suffix should return False."""
        assert is_human_readable_id("quick-fox-jumps-1a2b") is False

    def test_negative_suffix_invalid(self) -> None:
        """Negative suffix should return False (hyphen creates 5 parts)."""
        assert is_human_readable_id("quick-fox-jumps--1") is False

    def test_decimal_suffix_invalid(self) -> None:
        """Decimal suffix should return False."""
        assert is_human_readable_id("quick-fox-jumps-1.5") is False

    # --- Edge cases ---

    def test_consecutive_separators_invalid(self) -> None:
        """Consecutive separators should return False."""
        assert is_human_readable_id("quick--fox-jumps") is False

    def test_trailing_separator_invalid(self) -> None:
        """Trailing separator should return False."""
        assert is_human_readable_id("quick-fox-jumps-") is False

    def test_leading_separator_invalid(self) -> None:
        """Leading separator should return False."""
        assert is_human_readable_id("-quick-fox-jumps") is False

    def test_returns_bool(self) -> None:
        """Should return a boolean."""
        result = is_human_readable_id("test-id-here")
        assert isinstance(result, bool)

    def test_wrong_separator_format(self) -> None:
        """Using wrong separator should return False."""
        # Input has "-" but we expect "_"
        assert is_human_readable_id("quick-fox-jumps", separator="_") is False

    def test_valid_with_underscore_separator(self) -> None:
        """Valid ID with underscore separator should pass."""
        assert is_human_readable_id("quick_fox_jumps", separator="_") is True


# =============================================================================
# Tests: is_valid_word
# =============================================================================


class TestIsValidWord:
    """Tests for is_valid_word function."""

    # --- Valid words ---

    def test_valid_adjective(self) -> None:
        """Should return True for valid adjective."""
        assert is_valid_word("quick", ADJECTIVES) is True

    def test_valid_noun(self) -> None:
        """Should return True for valid noun."""
        assert is_valid_word("fox", NOUNS) is True

    def test_valid_verb(self) -> None:
        """Should return True for valid verb."""
        assert is_valid_word("jumps", VERBS) is True

    def test_first_adjective(self) -> None:
        """Should return True for first adjective."""
        assert is_valid_word("able", ADJECTIVES) is True

    def test_last_adjective(self) -> None:
        """Should return True for last adjective."""
        assert is_valid_word("witty", ADJECTIVES) is True

    def test_first_noun(self) -> None:
        """Should return True for first noun."""
        assert is_valid_word("ant", NOUNS) is True

    def test_last_noun(self) -> None:
        """Should return True for last noun."""
        assert is_valid_word("wind", NOUNS) is True

    def test_first_verb(self) -> None:
        """Should return True for first verb."""
        assert is_valid_word("dances", VERBS) is True

    def test_last_verb(self) -> None:
        """Should return True for last verb."""
        assert is_valid_word("waves", VERBS) is True

    # --- Invalid words ---

    def test_invalid_adjective(self) -> None:
        """Should return False for word not in adjectives."""
        assert is_valid_word("notaword", ADJECTIVES) is False

    def test_noun_not_in_adjectives(self) -> None:
        """Noun should not be in adjectives list."""
        assert is_valid_word("fox", ADJECTIVES) is False

    def test_verb_not_in_nouns(self) -> None:
        """Verb should not be in nouns list."""
        assert is_valid_word("jumps", NOUNS) is False

    def test_empty_string(self) -> None:
        """Empty string should return False."""
        assert is_valid_word("", ADJECTIVES) is False

    def test_case_sensitive(self) -> None:
        """Word lookup should be case-sensitive."""
        assert is_valid_word("QUICK", ADJECTIVES) is False
        assert is_valid_word("Quick", ADJECTIVES) is False

    def test_returns_bool(self) -> None:
        """Should return a boolean."""
        result = is_valid_word("test", ADJECTIVES)
        assert isinstance(result, bool)


# =============================================================================
# Tests: parse_human_readable_id
# =============================================================================


class TestParseHumanReadableId:
    """Tests for parse_human_readable_id function."""

    # --- Valid IDs ---

    def test_parse_three_part_id(self) -> None:
        """Should parse three-part ID correctly."""
        result = parse_human_readable_id("quick-fox-jumps")
        assert result == ("quick", "fox", "jumps", None)

    def test_parse_four_part_id(self) -> None:
        """Should parse four-part ID with suffix correctly."""
        result = parse_human_readable_id("quick-fox-jumps-123")
        assert result == ("quick", "fox", "jumps", 123)

    def test_parse_custom_separator(self) -> None:
        """Should parse ID with custom separator."""
        result = parse_human_readable_id("quick_fox_jumps", separator="_")
        assert result == ("quick", "fox", "jumps", None)

    def test_parse_custom_separator_with_suffix(self) -> None:
        """Should parse ID with custom separator and suffix."""
        result = parse_human_readable_id("quick_fox_jumps_456", separator="_")
        assert result == ("quick", "fox", "jumps", 456)

    def test_suffix_zero(self) -> None:
        """Should parse suffix=0 correctly."""
        result = parse_human_readable_id("calm-cat-sleeps-0")
        assert result == ("calm", "cat", "sleeps", 0)

    def test_large_suffix(self) -> None:
        """Should parse large suffix correctly."""
        result = parse_human_readable_id("happy-dog-plays-9999999")
        assert result == ("happy", "dog", "plays", 9999999)

    # --- Invalid IDs ---

    def test_invalid_too_few_parts(self) -> None:
        """Should return None for too few parts."""
        assert parse_human_readable_id("just-two") is None

    def test_invalid_too_many_parts(self) -> None:
        """Should return None for too many parts."""
        assert parse_human_readable_id("too-many-parts-here-now") is None

    def test_invalid_empty_string(self) -> None:
        """Should return None for empty string."""
        assert parse_human_readable_id("") is None

    def test_invalid_non_numeric_suffix(self) -> None:
        """Should return None for non-numeric suffix."""
        assert parse_human_readable_id("quick-fox-jumps-abc") is None

    def test_invalid_uppercase(self) -> None:
        """Should return None for uppercase letters."""
        assert parse_human_readable_id("Quick-fox-jumps") is None

    def test_invalid_special_chars(self) -> None:
        """Should return None for special characters."""
        assert parse_human_readable_id("quick-fox-jumps!") is None

    def test_invalid_with_spaces(self) -> None:
        """Should return None for IDs with spaces."""
        assert parse_human_readable_id("quick fox jumps") is None

    # --- Return type ---

    def test_returns_tuple_or_none(self) -> None:
        """Should return tuple or None."""
        valid_result = parse_human_readable_id("valid-id-here")
        assert valid_result is None or isinstance(valid_result, tuple)

    def test_returns_four_element_tuple(self) -> None:
        """Should return tuple with 4 elements."""
        result = parse_human_readable_id("quick-fox-jumps-123")
        assert result is not None
        assert len(result) == 4

    def test_suffix_is_int_or_none(self) -> None:
        """Suffix should be int or None."""
        result_with_suffix = parse_human_readable_id("quick-fox-jumps-123")
        assert result_with_suffix is not None
        assert isinstance(result_with_suffix[3], int)

        result_without_suffix = parse_human_readable_id("quick-fox-jumps")
        assert result_without_suffix is not None
        assert result_without_suffix[3] is None

    # --- Round trip ---

    def test_roundtrip_without_suffix(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """Parsing generated ID should give back original words."""
        generated = generate_human_readable_id(deterministic_first)
        parsed = parse_human_readable_id(generated)

        assert parsed is not None
        # Re-format should match
        reformatted = format_human_readable_id(parsed[0], parsed[1], parsed[2])
        assert reformatted == generated

    def test_roundtrip_with_suffix(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """Parsing generated ID with suffix should preserve suffix."""
        generated = generate_human_readable_id(deterministic_first, suffix=42)
        parsed = parse_human_readable_id(generated)

        assert parsed is not None
        assert parsed[3] == 42

        # Re-format should match
        reformatted = format_human_readable_id(parsed[0], parsed[1], parsed[2], suffix=parsed[3])
        assert reformatted == generated


# =============================================================================
# Tests: Collision Handling Simulation
# =============================================================================


class TestCollisionHandling:
    """Tests simulating collision handling behavior.

    These tests verify the expected usage pattern for handling collisions:
    1. Generate ID without suffix
    2. Check if exists
    3. If collision, retry with suffix (1, 2, 3, ...)
    """

    def test_basic_collision_resolution_pattern(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """Should be able to generate unique IDs by adding suffix on collision."""
        # Simulate: First attempt generates base ID
        id1 = generate_human_readable_id(deterministic_first, suffix=None)
        assert id1 == "able-ant-dances"

        # Simulate: Collision detected, retry with suffix
        id2 = generate_human_readable_id(deterministic_first, suffix=1)
        assert id2 == "able-ant-dances-1"

        # Both should be valid
        assert is_human_readable_id(id1)
        assert is_human_readable_id(id2)

        # They should be different
        assert id1 != id2

    def test_multiple_collision_retries(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """Should be able to generate multiple unique IDs with suffixes."""
        base = generate_human_readable_id(deterministic_first)

        # Simulate multiple collision retries
        ids = [base]
        for suffix in range(1, 11):
            new_id = generate_human_readable_id(deterministic_first, suffix=suffix)
            ids.append(new_id)

        # All IDs should be unique
        assert len(ids) == len(set(ids))

        # All should be valid
        for id_str in ids:
            assert is_human_readable_id(id_str)

    def test_suffix_parsing_for_collision_check(self) -> None:
        """Parsing should correctly identify suffix for collision tracking."""
        # When we have "quick-fox-jumps-5", we should know:
        parsed = parse_human_readable_id("quick-fox-jumps-5")
        assert parsed is not None
        assert parsed[3] == 5  # Suffix is 5
        assert parsed[0:3] == ("quick", "fox", "jumps")  # Base words

    def test_distinguishing_base_from_suffixed(self) -> None:
        """Should be able to distinguish base ID from suffixed version."""
        base = parse_human_readable_id("quick-fox-jumps")
        suffixed = parse_human_readable_id("quick-fox-jumps-5")

        assert base is not None
        assert suffixed is not None

        # Same base words
        assert base[0:3] == suffixed[0:3]

        # Different suffixes
        assert base[3] is None
        assert suffixed[3] == 5

    def test_sequential_suffix_generation_pattern(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """Verify typical sequential suffix pattern for collision resolution."""
        # Typical pattern: try 1, 2, 3... until available
        base_id = generate_human_readable_id(deterministic_first)

        # Simulate collision at suffix 1, 2, 3
        existing_ids = {base_id}
        for i in range(1, 4):
            existing_ids.add(f"{base_id}-{i}")

        # Find next available
        suffix = 1
        while generate_human_readable_id(deterministic_first, suffix=suffix) in existing_ids:
            suffix += 1

        # Should find 4 as available
        assert suffix == 4
        new_id = generate_human_readable_id(deterministic_first, suffix=suffix)
        assert new_id not in existing_ids

    def test_validate_collision_handling_integration(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """End-to-end collision handling simulation."""
        used_ids: set[str] = set()

        def generate_unique_id(existing: set[str], max_attempts: int = 100) -> str:
            """Generate a unique ID, adding suffix on collision."""
            base_id = generate_human_readable_id(deterministic_first)

            if base_id not in existing:
                return base_id

            for suffix in range(1, max_attempts + 1):
                candidate = generate_human_readable_id(deterministic_first, suffix=suffix)
                if candidate not in existing:
                    return candidate

            raise RuntimeError(f"Could not generate unique ID after {max_attempts} attempts")

        # Generate several IDs
        for _ in range(10):
            new_id = generate_unique_id(used_ids)
            used_ids.add(new_id)

        # All should be unique
        assert len(used_ids) == 10

        # All should be valid
        for id_str in used_ids:
            assert is_human_readable_id(id_str), f"Invalid ID: {id_str}"


# =============================================================================
# Tests: Statistics Functions
# =============================================================================


class TestStatisticsFunctions:
    """Tests for statistics functions."""

    def test_calculate_total_combinations(self) -> None:
        """Should calculate correct number of combinations."""
        total = calculate_total_combinations()
        expected = len(ADJECTIVES) * len(NOUNS) * len(VERBS)
        assert total == expected

    def test_total_combinations_greater_than_thousand(self) -> None:
        """Should have at least 1000 unique combinations."""
        total = calculate_total_combinations()
        assert total > 1000

    def test_total_combinations_greater_than_ten_thousand(self) -> None:
        """Should have at least 10000 unique combinations."""
        total = calculate_total_combinations()
        assert total > 10000

    def test_get_unique_word_count(self) -> None:
        """Should return correct total word count."""
        count = get_unique_word_count()
        expected = len(ADJECTIVES) + len(NOUNS) + len(VERBS)
        assert count == expected

    def test_word_count_at_least_hundred(self) -> None:
        """Should have at least 100 words total."""
        count = get_unique_word_count()
        assert count >= 100

    def test_returns_integer(self) -> None:
        """Statistics functions should return integers."""
        assert isinstance(calculate_total_combinations(), int)
        assert isinstance(get_unique_word_count(), int)

    def test_combinations_are_positive(self) -> None:
        """Statistics should be positive numbers."""
        assert calculate_total_combinations() > 0
        assert get_unique_word_count() > 0


# =============================================================================
# Tests: Word Lists Quality
# =============================================================================


class TestWordListsQuality:
    """Tests for word list quality and consistency."""

    def test_adjectives_all_lowercase(self) -> None:
        """All adjectives should be lowercase."""
        for adj in ADJECTIVES:
            assert adj.islower(), f"Adjective '{adj}' is not lowercase"

    def test_nouns_all_lowercase(self) -> None:
        """All nouns should be lowercase."""
        for noun in NOUNS:
            assert noun.islower(), f"Noun '{noun}' is not lowercase"

    def test_verbs_all_lowercase(self) -> None:
        """All verbs should be lowercase."""
        for verb in VERBS:
            assert verb.islower(), f"Verb '{verb}' is not lowercase"

    def test_adjectives_all_alpha(self) -> None:
        """All adjectives should be alphabetic."""
        for adj in ADJECTIVES:
            assert adj.isalpha(), f"Adjective '{adj}' contains non-alpha chars"

    def test_nouns_all_alpha(self) -> None:
        """All nouns should be alphabetic."""
        for noun in NOUNS:
            assert noun.isalpha(), f"Noun '{noun}' contains non-alpha chars"

    def test_verbs_all_alpha(self) -> None:
        """All verbs should be alphabetic."""
        for verb in VERBS:
            assert verb.isalpha(), f"Verb '{verb}' contains non-alpha chars"

    def test_adjectives_no_duplicates(self) -> None:
        """Adjectives should have no duplicates."""
        assert len(ADJECTIVES) == len(set(ADJECTIVES))

    def test_nouns_no_duplicates(self) -> None:
        """Nouns should have no duplicates."""
        assert len(NOUNS) == len(set(NOUNS))

    def test_verbs_no_duplicates(self) -> None:
        """Verbs should have no duplicates."""
        assert len(VERBS) == len(set(VERBS))

    def test_adjectives_non_empty(self) -> None:
        """Adjectives list should not be empty."""
        assert len(ADJECTIVES) > 0

    def test_nouns_non_empty(self) -> None:
        """Nouns list should not be empty."""
        assert len(NOUNS) > 0

    def test_verbs_non_empty(self) -> None:
        """Verbs list should not be empty."""
        assert len(VERBS) > 0

    def test_no_empty_strings_in_adjectives(self) -> None:
        """No adjectives should be empty strings."""
        for adj in ADJECTIVES:
            assert len(adj) > 0

    def test_no_empty_strings_in_nouns(self) -> None:
        """No nouns should be empty strings."""
        for noun in NOUNS:
            assert len(noun) > 0

    def test_no_empty_strings_in_verbs(self) -> None:
        """No verbs should be empty strings."""
        for verb in VERBS:
            assert len(verb) > 0


# =============================================================================
# Integration Tests: Full Workflow
# =============================================================================


class TestFullWorkflow:
    """End-to-end tests for the complete ID workflow."""

    def test_generate_validate_parse_cycle(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """Complete cycle: generate -> validate -> parse -> re-format."""
        # Generate
        original = generate_human_readable_id(deterministic_first, suffix=42)

        # Validate
        assert is_human_readable_id(original)

        # Parse
        parsed = parse_human_readable_id(original)
        assert parsed is not None

        # Re-format
        reconstructed = format_human_readable_id(parsed[0], parsed[1], parsed[2], suffix=parsed[3])

        # Should match
        assert reconstructed == original

    def test_different_separators_workflow(self, deterministic_first: Callable[[list[str]], str]) -> None:
        """Workflow should work with different separators."""
        # Generate with underscore
        id_underscore = generate_human_readable_id(deterministic_first, separator="_", suffix=10)

        # Validate with same separator
        assert is_human_readable_id(id_underscore, separator="_")

        # Parse with same separator
        parsed = parse_human_readable_id(id_underscore, separator="_")
        assert parsed is not None

        # Re-format with same separator
        reconstructed = format_human_readable_id(
            parsed[0], parsed[1], parsed[2], separator="_", suffix=parsed[3]
        )
        assert reconstructed == id_underscore

    def test_format_parse_roundtrip_various_inputs(self) -> None:
        """Format and parse should be inverses for various inputs."""
        test_cases = [
            ("a", "b", "c", None),
            ("quick", "fox", "jumps", None),
            ("quick", "fox", "jumps", 1),
            ("quick", "fox", "jumps", 12345),
            ("zebra", "ant", "flies", 0),
        ]

        for adj, noun, verb, suffix in test_cases:
            formatted = format_human_readable_id(adj, noun, verb, suffix=suffix)
            parsed = parse_human_readable_id(formatted)
            assert parsed == (adj, noun, verb, suffix), f"Failed for {formatted}"
