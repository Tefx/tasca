"""
Unit tests for Mention resolution service.

Tests cover:
1. Mention parsing from content
2. UUID format detection
3. Single mention resolution (alias, display_name, UUID, ambiguous, unresolved)
4. Multi-mention resolution
5. @all special handling
6. Unresolved limit validation
"""

import pytest

from tasca.core.domain.patron import PatronId
from tasca.core.services.mention_service import (
    AmbiguousMention,
    MentionsResult,
    PatronMatch,
    ResolvedMention,
    UnresolvedMention,
    get_resolved_patron_ids,
    get_unresolved_handles,
    get_unresolved_handles_from_result,
    has_ambiguous_mentions,
    is_uuid_format,
    parse_mentions,
    resolve_mentions,
    resolve_single_mention,
    validate_unresolved_limit,
)


class TestParseMentions:
    """Tests for parse_mentions function."""

    def test_empty_content(self) -> None:
        """Empty content returns empty list."""
        assert parse_mentions("") == []

    def test_no_mentions(self) -> None:
        """Content without mentions returns empty list."""
        assert parse_mentions("Hello world") == []
        assert parse_mentions("No @ symbol here") == []

    def test_single_mention(self) -> None:
        """Single mention is extracted."""
        assert parse_mentions("Hello @alice") == ["alice"]

    def test_multiple_mentions(self) -> None:
        """Multiple mentions are extracted in order."""
        assert parse_mentions("Hello @alice and @bob") == ["alice", "bob"]

    def test_duplicate_mentions_deduplicated(self) -> None:
        """Duplicate mentions are deduplicated but order preserved."""
        assert parse_mentions("@alice @bob @alice @charlie") == ["alice", "bob", "charlie"]

    def test_case_preserved(self) -> None:
        """Original case is preserved (case-insensitive matching)."""
        assert parse_mentions("@Alice @BOB") == ["Alice", "BOB"]

    def test_all_special_mention(self) -> None:
        """@all is recognized as a mention."""
        assert parse_mentions("@all please respond") == ["all"]

    def test_hyphenated_handles(self) -> None:
        """Handles with hyphens are supported."""
        assert parse_mentions("@user-123") == ["user-123"]

    def test_underscore_handles(self) -> None:
        """Handles with underscores are supported."""
        assert parse_mentions("@test_name") == ["test_name"]

    def test_multiple_at_symbols(self) -> None:
        """Multiple @ symbols only capture the last handle."""
        assert parse_mentions("@@double") == ["double"]
        assert parse_mentions("@@@triple") == ["triple"]

    def test_mention_at_end(self) -> None:
        """Mention at end of content is captured."""
        assert parse_mentions("Hello @alice") == ["alice"]

    def test_mention_at_start(self) -> None:
        """Mention at start of content is captured."""
        assert parse_mentions("@alice hello") == ["alice"]

    def test_adjacent_mentions(self) -> None:
        """Adjacent mentions are both captured."""
        assert parse_mentions("@alice@bob") == ["alice", "bob"]


class TestIsUuidFormat:
    """Tests for is_uuid_format function."""

    def test_valid_uuid_lowercase(self) -> None:
        """Valid lowercase UUID is detected."""
        assert is_uuid_format("550e8400-e29b-41d4-a716-446655440000") is True

    def test_valid_uuid_uppercase(self) -> None:
        """Valid uppercase UUID is detected."""
        assert is_uuid_format("550E8400-E29B-41D4-A716-446655440000") is True

    def test_valid_uuid_mixed_case(self) -> None:
        """Valid mixed case UUID is detected."""
        assert is_uuid_format("550e8400-E29b-41D4-A716-446655440000") is True

    def test_invalid_uuid_short(self) -> None:
        """Short string is not a UUID."""
        assert is_uuid_format("alice") is False

    def test_invalid_uuid_wrong_format(self) -> None:
        """Malformed UUID is not detected."""
        assert is_uuid_format("550e8400-e29b-41d4-a716") is False  # Too short
        assert is_uuid_format("not-a-uuid") is False
        assert is_uuid_format("550e8400e29b41d4a716446655440000") is False  # No dashes


class TestResolveSingleMention:
    """Tests for resolve_single_mention function."""

    @pytest.fixture
    def alice(self) -> PatronMatch:
        """Patron with alias 'alice'."""
        return PatronMatch(PatronId("p-001"), "alice", "Alice Bot")

    @pytest.fixture
    def bob(self) -> PatronMatch:
        """Patron with alias 'bob'."""
        return PatronMatch(PatronId("p-002"), "bob", "Bob Bot")

    @pytest.fixture
    def alice_display_only(self) -> PatronMatch:
        """Patron without alias, display_name only."""
        return PatronMatch(PatronId("p-003"), None, "Alice")

    def test_uuid_format_direct_resolution(self) -> None:
        """UUID format resolves directly to patron_id."""
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        result = resolve_single_mention(uuid, [])
        assert isinstance(result, ResolvedMention)
        assert result.patron_id == PatronId(uuid)

    def test_alias_exact_match(self, alice: PatronMatch, bob: PatronMatch) -> None:
        """Exact alias match resolves."""
        result = resolve_single_mention("alice", [alice, bob])
        assert isinstance(result, ResolvedMention)
        assert result.patron_id == PatronId("p-001")

    def test_alias_case_insensitive(self, alice: PatronMatch) -> None:
        """Alias match is case-insensitive."""
        result = resolve_single_mention("ALICE", [alice])
        assert isinstance(result, ResolvedMention)
        assert result.patron_id == PatronId("p-001")

    def test_display_name_match(self, alice_display_only: PatronMatch) -> None:
        """Display name match when no alias matches."""
        result = resolve_single_mention("Alice", [alice_display_only])
        assert isinstance(result, ResolvedMention)
        assert result.patron_id == PatronId("p-003")

    def test_display_name_case_insensitive(self, alice_display_only: PatronMatch) -> None:
        """Display name match is case-insensitive."""
        result = resolve_single_mention("alice", [alice_display_only])
        assert isinstance(result, ResolvedMention)
        assert result.patron_id == PatronId("p-003")

    def test_alias_priority_over_display_name(
        self, alice: PatronMatch, alice_display_only: PatronMatch
    ) -> None:
        """Alias match and display name match on same string is ambiguous.

        This is the CORRECT behavior: when two patrons match the same handle
        (one by alias, one by display_name), it's ambiguous and the client
        must disambiguate.
        """
        # alice has alias="alice", display_name="Alice Bot"
        # alice_display_only has display_name="Alice"
        # Resolving "alice": alice matches by alias, alice_display_only matches by display_name
        # Case-insensitive: "alice" == "Alice" -> both match -> ambiguous
        result = resolve_single_mention("alice", [alice, alice_display_only])
        assert isinstance(result, AmbiguousMention)
        assert len(result.candidates) == 2

    def test_ambiguous_multiple_display_name_matches(self) -> None:
        """Multiple display name matches returns AmbiguousMention."""
        patron1 = PatronMatch(PatronId("p-001"), None, "Alice")
        patron2 = PatronMatch(PatronId("p-002"), None, "Alice")
        result = resolve_single_mention("Alice", [patron1, patron2])
        assert isinstance(result, AmbiguousMention)
        assert len(result.candidates) == 2

    def test_alias_unique_not_ambiguous(self) -> None:
        """When only one patron matches (by unique alias), it resolves."""
        # Create patrons with unique aliases that don't match each other's display names
        alice = PatronMatch(PatronId("p-001"), "alice", "Alice Bot")
        bob = PatronMatch(PatronId("p-002"), "bob", "Bob Bot")
        result = resolve_single_mention("alice", [alice, bob])
        assert isinstance(result, ResolvedMention)
        assert result.patron_id == PatronId("p-001")

    def test_ambiguous_same_display_name_no_alias(self) -> None:
        """Ambiguous when two patrons have same display_name and no alias."""
        patron1 = PatronMatch(PatronId("p-001"), None, "Bob")
        patron2 = PatronMatch(PatronId("p-002"), None, "Bob")
        result = resolve_single_mention("bob", [patron1, patron2])
        assert isinstance(result, AmbiguousMention)
        assert len(result.candidates) == 2

    def test_unresolved_no_match(self, alice: PatronMatch) -> None:
        """No match returns UnresolvedMention."""
        result = resolve_single_mention("charlie", [alice])
        assert isinstance(result, UnresolvedMention)
        assert result.handle == "charlie"

    def test_empty_patrons_list_unresolved(self) -> None:
        """Empty patrons list results in UnresolvedMention."""
        result = resolve_single_mention("550e8400-e29b-41d4-a716-446655440000", [])
        # UUID format is resolved directly
        assert isinstance(result, ResolvedMention)

        result = resolve_single_mention("alice", [])
        assert isinstance(result, UnresolvedMention)


class TestResolveMentions:
    """Tests for resolve_mentions function."""

    @pytest.fixture
    def alice(self) -> PatronMatch:
        """Patron with alias 'alice'."""
        return PatronMatch(PatronId("p-001"), "alice", "Alice Bot")

    @pytest.fixture
    def bob(self) -> PatronMatch:
        """Patron with alias 'bob'."""
        return PatronMatch(PatronId("p-002"), "bob", "Bob Bot")

    def test_empty_mentions(self, alice: PatronMatch) -> None:
        """Empty mentions list returns empty result."""
        result = resolve_mentions([], [alice])
        assert result.resolved == []
        assert result.unresolved == []
        assert result.ambiguous == []
        assert result.mentions_all is False

    def test_empty_patrons(self) -> None:
        """Empty patrons with mentions results in unresolved."""
        result = resolve_mentions(["alice", "bob"], [])
        assert len(result.unresolved) == 2
        assert result.mentions_all is False

    def test_all_mention(self, alice: PatronMatch) -> None:
        """@all sets mentions_all flag."""
        result = resolve_mentions(["all"], [alice])
        assert result.mentions_all is True
        assert result.resolved == []
        assert result.unresolved == []

    def test_all_case_insensitive(self, alice: PatronMatch) -> None:
        """@ALL (any case) sets mentions_all flag."""
        result = resolve_mentions(["ALL"], [alice])
        assert result.mentions_all is True

    def test_mixed_mentions(self, alice: PatronMatch, bob: PatronMatch) -> None:
        """Mix of resolved, unresolved, and @all."""
        result = resolve_mentions(["alice", "charlie", "all"], [alice, bob])
        assert len(result.resolved) == 1
        assert result.resolved[0].handle == "alice"
        assert len(result.unresolved) == 1
        assert result.unresolved[0].handle == "charlie"
        assert result.mentions_all is True

    def test_all_resolved(self, alice: PatronMatch, bob: PatronMatch) -> None:
        """All mentions resolved."""
        result = resolve_mentions(["alice", "bob"], [alice, bob])
        assert len(result.resolved) == 2
        assert {r.handle for r in result.resolved} == {"alice", "bob"}
        assert result.unresolved == []
        assert result.ambiguous == []

    def test_all_unresolved(self) -> None:
        """All mentions unresolved."""
        result = resolve_mentions(["alice", "bob"], [])
        assert len(result.unresolved) == 2
        assert {u.handle for u in result.unresolved} == {"alice", "bob"}

    def test_uuid_in_mentions(self) -> None:
        """UUID in mentions resolves directly."""
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        result = resolve_mentions([uuid], [])
        assert len(result.resolved) == 1
        assert result.resolved[0].patron_id == PatronId(uuid)

    def test_duplicate_mentions_single_result(self, alice: PatronMatch) -> None:
        """Duplicate mentions in parse_mentions are already deduped."""
        # Note: parse_mentions deduplicates, but if caller passes duplicates:
        result = resolve_mentions(["alice", "alice"], [alice])
        # Both resolve to same patron
        assert len(result.resolved) == 2
        assert all(r.patron_id == PatronId("p-001") for r in result.resolved)


class TestValidateUnresolvedLimit:
    """Tests for validate_unresolved_limit function."""

    def test_zero_unresolved(self) -> None:
        """Zero unresolved is always valid."""
        assert validate_unresolved_limit(0, 0) is True
        assert validate_unresolved_limit(0, 10) is True

    def test_at_limit(self) -> None:
        """At limit is valid."""
        assert validate_unresolved_limit(10, 10) is True
        assert validate_unresolved_limit(5, 5) is True

    def test_over_limit(self) -> None:
        """Over limit is invalid."""
        assert validate_unresolved_limit(11, 10) is False
        assert validate_unresolved_limit(6, 5) is False

    def test_under_limit(self) -> None:
        """Under limit is valid."""
        assert validate_unresolved_limit(5, 10) is True
        assert validate_unresolved_limit(1, 10) is True


class TestGetUnresolvedHandles:
    """Tests for get_unresolved_handles function."""

    @pytest.fixture
    def alice(self) -> PatronMatch:
        """Patron with alias 'alice'."""
        return PatronMatch(PatronId("p-001"), "alice", "Alice Bot")

    def test_all_resolved(self, alice: PatronMatch) -> None:
        """All resolved returns empty list."""
        assert get_unresolved_handles(["alice"], [alice]) == []

    def test_all_unresolved(self, alice: PatronMatch) -> None:
        """Unresolved handles are returned."""
        assert get_unresolved_handles(["bob"], [alice]) == ["bob"]

    def test_mixed(self, alice: PatronMatch) -> None:
        """Mixed resolved/unresolved."""
        assert get_unresolved_handles(["alice", "bob"], [alice]) == ["bob"]


class TestHelperFunctions:
    """Tests for helper functions."""

    @pytest.fixture
    def alice(self) -> PatronMatch:
        """Patron with alias 'alice'."""
        return PatronMatch(PatronId("p-001"), "alice", "Alice Bot")

    def test_get_resolved_patron_ids(self, alice: PatronMatch) -> None:
        """get_resolved_patron_ids extracts patron IDs."""
        result = resolve_mentions(["alice"], [alice])
        pids = get_resolved_patron_ids(result)
        assert pids == [PatronId("p-001")]

    def test_get_unresolved_handles_from_result(self, alice: PatronMatch) -> None:
        """get_unresolved_handles_from_result extracts handles."""
        result = resolve_mentions(["bob"], [alice])
        handles = get_unresolved_handles_from_result(result)
        assert handles == ["bob"]

    def test_has_ambiguous_mentions_false(self, alice: PatronMatch) -> None:
        """has_ambiguous_mentions returns False for clear resolution."""
        result = resolve_mentions(["alice"], [alice])
        assert has_ambiguous_mentions(result) is False

    def test_has_ambiguous_mentions_true(self) -> None:
        """has_ambiguous_mentions returns True for ambiguous resolution."""
        patron1 = PatronMatch(PatronId("p-001"), None, "Alice")
        patron2 = PatronMatch(PatronId("p-002"), None, "Alice")
        result = resolve_mentions(["Alice"], [patron1, patron2])
        assert has_ambiguous_mentions(result) is True


class TestAmbiguousMentionResponse:
    """Test cases demonstrating AmbiguousMention response structure."""

    def test_ambiguous_mention_structure(self) -> None:
        """AmbiguousMention contains handle and candidates."""
        patron1 = PatronMatch(PatronId("p-001"), None, "Bob")
        patron2 = PatronMatch(PatronId("p-002"), None, "Bob")
        result = resolve_single_mention("Bob", [patron1, patron2])

        assert isinstance(result, AmbiguousMention)
        assert result.handle == "Bob"
        assert len(result.candidates) == 2

        # Verify candidates have required fields
        for candidate in result.candidates:
            # PatronId is a NewType (str), so check it's a string
            assert isinstance(candidate.patron_id, str)
            assert candidate.display_name == "Bob"

    def test_ambiguous_in_full_result(self) -> None:
        """Ambiguous mention appears in MentionsResult.ambiguous."""
        patron1 = PatronMatch(PatronId("p-001"), None, "Alice")
        patron2 = PatronMatch(PatronId("p-002"), None, "Alice")
        result = resolve_mentions(["Alice"], [patron1, patron2])

        assert len(result.ambiguous) == 1
        assert result.ambiguous[0].handle == "Alice"
        assert len(result.ambiguous[0].candidates) == 2


class TestMentionsUnresolvedResponse:
    """Test cases demonstrating mentions_unresolved response structure."""

    def test_unresolved_in_result(self) -> None:
        """Unresolved mentions appear in MentionsResult.unresolved."""
        alice = PatronMatch(PatronId("p-001"), "alice", "Alice Bot")
        result = resolve_mentions(["alice", "charlie", "dave"], [alice])

        assert len(result.resolved) == 1
        assert len(result.unresolved) == 2
        assert {u.handle for u in result.unresolved} == {"charlie", "dave"}

    def test_unresolved_handles_extraction(self) -> None:
        """Unresolved handles can be extracted for storage."""
        alice = PatronMatch(PatronId("p-001"), "alice", "Alice Bot")
        result = resolve_mentions(["charlie", "dave"], [alice])

        unresolved_handles = get_unresolved_handles_from_result(result)
        assert unresolved_handles == ["charlie", "dave"]


class TestResolutionMatrix:
    """Comprehensive test matrix for all resolution rules."""

    @pytest.fixture
    def patrons(self) -> list[PatronMatch]:
        """Sample patrons for testing."""
        return [
            PatronMatch(PatronId("p-001"), "alice", "Alice Bot"),
            PatronMatch(PatronId("p-002"), "bob", "Bob Bot"),
            PatronMatch(PatronId("p-003"), None, "Charlie"),
            PatronMatch(PatronId("p-004"), "dave", "Dave"),
        ]

    def test_resolution_by_uuid(self) -> None:
        """Rule 1: UUID format resolves directly."""
        uuid = "550e8400-e29b-41d4-a716-446655440000"
        result = resolve_single_mention(uuid, [])
        assert isinstance(result, ResolvedMention)
        assert result.patron_id == PatronId(uuid)

    def test_resolution_by_alias(self, patrons: list[PatronMatch]) -> None:
        """Rule 2: Exact alias match resolves."""
        result = resolve_single_mention("alice", patrons)
        assert isinstance(result, ResolvedMention)
        assert result.patron_id == PatronId("p-001")

    def test_resolution_by_display_name(self, patrons: list[PatronMatch]) -> None:
        """Rule 3: Exact display_name match when no alias matches."""
        result = resolve_single_mention("Charlie", patrons)
        assert isinstance(result, ResolvedMention)
        assert result.patron_id == PatronId("p-003")

    def test_ambiguous_multiple_matches(self, patrons: list[PatronMatch]) -> None:
        """Rule 4: Multiple matches returns AmbiguousMention."""
        # Add another patron with same display_name
        patrons_with_dupe = patrons + [PatronMatch(PatronId("p-005"), None, "Charlie")]
        result = resolve_single_mention("Charlie", patrons_with_dupe)
        assert isinstance(result, AmbiguousMention)
        assert len(result.candidates) == 2

    def test_unresolved_no_match(self, patrons: list[PatronMatch]) -> None:
        """Rule 5: No match returns UnresolvedMention."""
        result = resolve_single_mention("unknown", patrons)
        assert isinstance(result, UnresolvedMention)
        assert result.handle == "unknown"

    def test_case_insensitive_resolution(self, patrons: list[PatronMatch]) -> None:
        """Resolution is case-insensitive."""
        result1 = resolve_single_mention("ALICE", patrons)
        result2 = resolve_single_mention("alice", patrons)
        result3 = resolve_single_mention("Alice", patrons)

        assert isinstance(result1, ResolvedMention)
        assert isinstance(result2, ResolvedMention)
        assert isinstance(result3, ResolvedMention)
        # All should resolve to same patron
        assert result1.patron_id == result2.patron_id == result3.patron_id
