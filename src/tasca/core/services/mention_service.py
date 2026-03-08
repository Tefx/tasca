"""
Mention resolution service - core business logic for @mention handling.

This module provides pure functions for resolving mention handles to patron IDs.

Resolution rules (priority order):
1. "@all" → sets mentions_all flag, no patron_id resolution
2. UUID format → treated as direct patron_id
3. Exact alias match → resolved to patron_id
4. Exact display_name match → resolved to patron_id
5. Multiple matches → AmbiguousMention error
6. No matches → UnresolvedMention (recorded but accepted)

Limit: Max 10 unresolved mentions per saying (guardrail against mention spam).

This module is pure (no I/O) - patron data is passed as parameters.
"""

import re
from dataclasses import dataclass
from typing import NewType

import deal

from tasca.core.domain.patron import PatronId

# =============================================================================
# Types
# =============================================================================

# Regex pattern for @mentions: @ followed by non-whitespace, non-punctuation chars
MENTION_PATTERN = re.compile(r"@(\w[\w-]*)")
# UUID pattern (v4-like: 8-4-4-4-12 hex digits)
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PatronMatch:
    """A patron that could potentially match a mention.

    Used as input for resolution - typically queried from storage.
    """

    patron_id: PatronId
    alias: str | None
    display_name: str


@dataclass(frozen=True)
class ResolvedMention:
    """A successfully resolved mention to a patron_id."""

    handle: str  # Original mention text (e.g., "alice" or "uuid-string")
    patron_id: PatronId


@dataclass(frozen=True)
class AmbiguousMention:
    """Multiple patrons match the mention handle.

    This is an error - client must disambiguate before the saying can be written.
    """

    handle: str
    candidates: list[PatronMatch]


@dataclass(frozen=True)
class UnresolvedMention:
    """No patron matches the mention handle.

    The write is accepted, but the handle is recorded as unresolved.
    """

    handle: str


@dataclass(frozen=True)
class MentionsResult:
    """Result of resolving all mentions in a saying.

    Attributes:
        resolved: List of mentions successfully resolved to patron IDs.
        unresolved: List of mentions that couldn't be matched.
        ambiguous: List of mentions with multiple matches (error condition).
        mentions_all: True if @all was mentioned.
    """

    resolved: list[ResolvedMention]
    unresolved: list[UnresolvedMention]
    ambiguous: list[AmbiguousMention]
    mentions_all: bool


# =============================================================================
# Pure Functions
# =============================================================================


@deal.pre(lambda content: content is not None)
@deal.post(lambda result: isinstance(result, list))
@deal.post(lambda result: all(isinstance(m, str) for m in result))
@deal.post(lambda result: all(len(m) > 0 for m in result))
def parse_mentions(content: str) -> list[str]:
    """Extract mention handles from content.

    A mention is @ followed by word characters (letters, digits, underscore, hyphen).
    Mentions are case-insensitive for matching but the original case is preserved.

    The @ symbol is stripped from the returned handles.

    Args:
        content: The text content to parse for mentions.

    Returns:
        List of mention handles (without the @ prefix), in order of appearance.

    Examples:
        >>> parse_mentions("Hello @alice and @bob")
        ['alice', 'bob']
        >>> parse_mentions("@all please respond")
        ['all']
        >>> parse_mentions("No mentions here")
        []
        >>> parse_mentions("@user-123 @test_name")
        ['user-123', 'test_name']
        >>> parse_mentions("@@@ignore")  # Multiple @ symbols
        ['ignore']
        >>> parse_mentions("")  # Edge: empty content
        []
    """
    matches = MENTION_PATTERN.findall(content)
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for handle in matches:
        handle_lower = handle.lower()
        if handle_lower not in seen:
            seen.add(handle_lower)
            result.append(handle)
    return result


@deal.pre(lambda text: len(text) > 0)
@deal.post(lambda result: isinstance(result, bool))
def is_uuid_format(text: str) -> bool:
    """Check if text looks like a UUID.

    UUID detection allows clients to specify patron_id directly in mentions.

    Args:
        text: The text to check.

    Returns:
        True if text matches UUID format (case-insensitive).

    Examples:
        >>> is_uuid_format("550e8400-e29b-41d4-a716-446655440000")
        True
        >>> is_uuid_format("550E8400-E29B-41D4-A716-446655440000")  # Uppercase
        True
        >>> is_uuid_format("alice")
        False
        >>> is_uuid_format("not-a-uuid")
        False
    """
    return bool(UUID_PATTERN.match(text))


@deal.pre(lambda handle, patrons: len(handle) > 0 and patrons is not None)
@deal.post(
    lambda result: isinstance(result, (ResolvedMention, AmbiguousMention, UnresolvedMention))
)
def resolve_single_mention(
    handle: str,
    patrons: list[PatronMatch],
) -> ResolvedMention | AmbiguousMention | UnresolvedMention:
    """Resolve a single mention handle to a patron.

    Resolution priority:
    1. If handle is UUID format → resolved directly as patron_id (no lookup)
    2. Exact alias match (case-insensitive)
    3. Exact display_name match (case-insensitive)
    4. Multiple matches → AmbiguousMention
    5. No matches → UnresolvedMention

    Args:
        handle: The mention handle to resolve (without @ prefix).
        patrons: List of patrons that could match.

    Returns:
        ResolvedMention: Single match found.
        AmbiguousMention: Multiple matches found (error).
        UnresolvedMention: No matches found (accepted but unresolved).

    Examples:
        >>> alice = PatronMatch(PatronId("p-001"), "alice", "Alice Bot")
        >>> bob = PatronMatch(PatronId("p-002"), "bob", "Bob Bot")
        >>> # Single alias match
        >>> result = resolve_single_mention("alice", [alice, bob])
        >>> result.handle
        'alice'
        >>> result.patron_id
        'p-001'
        >>> # UUID format
        >>> result = resolve_single_mention("550e8400-e29b-41d4-a716-446655440000", [])
        >>> isinstance(result, ResolvedMention)
        True
        >>> # Unresolved
        >>> result = resolve_single_mention("charlie", [alice, bob])
        >>> isinstance(result, UnresolvedMention)
        True
    """
    # Rule 1: UUID format - direct resolution
    if is_uuid_format(handle):
        return ResolvedMention(handle=handle, patron_id=PatronId(handle))

    handle_lower = handle.lower()
    candidates: list[PatronMatch] = []

    for patron in patrons:
        # Rule 2: Exact alias match (case-insensitive)
        if patron.alias is not None and patron.alias.lower() == handle_lower:
            candidates.append(patron)
            continue  # Don't also match display_name if alias matched

        # Rule 3: Exact display_name match (case-insensitive)
        if patron.display_name.lower() == handle_lower:
            candidates.append(patron)

    # Rule 4: Multiple matches → Ambiguous
    if len(candidates) > 1:
        return AmbiguousMention(handle=handle, candidates=candidates)

    # Rule 5: Single match → Resolved
    if len(candidates) == 1:
        return ResolvedMention(handle=handle, patron_id=candidates[0].patron_id)

    # Rule 6: No matches → Unresolved
    return UnresolvedMention(handle=handle)


@deal.pre(
    lambda mentions, patrons, max_unresolved=10: (
        isinstance(mentions, list)
        and isinstance(patrons, list)
        and max_unresolved >= 0
        and len(mentions) >= 0
        and len(patrons) >= 0
    )
)
@deal.post(lambda result: isinstance(result, MentionsResult))
def resolve_mentions(
    mentions: list[str],
    patrons: list[PatronMatch],
    max_unresolved: int = 10,
) -> MentionsResult:
    """Resolve all mention handles to patron IDs.

    This is the main entry point for mention resolution.
    It processes each mention and categorizes the results.

    @all is a special mention that sets the mentions_all flag but doesn't
    resolve to any patron_id.

    Args:
        mentions: List of mention handles (without @ prefix).
        patrons: List of patrons that could match mentions.
        max_unresolved: Maximum allowed unresolved mentions (default 10).

    Returns:
        MentionsResult with resolved, unresolved, ambiguous, and mentions_all.

    Examples:
        >>> alice = PatronMatch(PatronId("p-001"), "alice", "Alice Bot")
        >>> bob = PatronMatch(PatronId("p-002"), "bob", "Bob Bot")
        >>> # Normal resolution
        >>> result = resolve_mentions(["alice", "bob"], [alice, bob])
        >>> len(result.resolved)
        2
        >>> result.mentions_all
        False
        >>> # @all mention
        >>> result = resolve_mentions(["all"], [alice, bob])
        >>> result.mentions_all
        True
        >>> len(result.resolved)
        0
        >>> # Empty mentions
        >>> result = resolve_mentions([], [alice, bob])
        >>> result.mentions_all
        False
        >>> len(result.resolved)
        0
    """
    resolved: list[ResolvedMention] = []
    unresolved: list[UnresolvedMention] = []
    ambiguous: list[AmbiguousMention] = []
    mentions_all = False

    for handle in mentions:
        # Special case: @all
        if handle.lower() == "all":
            mentions_all = True
            continue

        result = resolve_single_mention(handle, patrons)

        if isinstance(result, ResolvedMention):
            resolved.append(result)
        elif isinstance(result, AmbiguousMention):
            ambiguous.append(result)
        else:  # UnresolvedMention
            unresolved.append(result)

    return MentionsResult(
        resolved=resolved,
        unresolved=unresolved,
        ambiguous=ambiguous,
        mentions_all=mentions_all,
    )


@deal.pre(lambda unresolved_count, max_allowed: unresolved_count >= 0 and max_allowed >= 0)
@deal.post(lambda result: isinstance(result, bool))
def validate_unresolved_limit(unresolved_count: int, max_allowed: int) -> bool:
    """Validate that unresolved mention count is within allowed limit.

    This is a guardrail against mention spam.

    Args:
        unresolved_count: Number of unresolved mentions.
        max_allowed: Maximum allowed unresolved mentions.

    Returns:
        True if within limit, False otherwise.

    Examples:
        >>> validate_unresolved_limit(0, 10)
        True
        >>> validate_unresolved_limit(10, 10)
        True
        >>> validate_unresolved_limit(11, 10)
        False
        >>> validate_unresolved_limit(5, 5)
        True
    """
    return unresolved_count <= max_allowed


@deal.pre(
    lambda handles, patrons: (
        isinstance(handles, list)
        and isinstance(patrons, list)
        and len(handles) >= 0
        and len(patrons) >= 0
    )
)
@deal.post(lambda result: isinstance(result, list))
def get_unresolved_handles(
    handles: list[str],
    patrons: list[PatronMatch],
) -> list[str]:
    """Get the handles that would be unresolved given the available patrons.

    This is a helper for preview/validation scenarios.

    Args:
        handles: List of mention handles to check.
        patrons: List of available patrons.

    Returns:
        List of handles that would be unresolved.

    Examples:
        >>> alice = PatronMatch(PatronId("p-001"), "alice", "Alice Bot")
        >>> get_unresolved_handles(["alice", "bob"], [alice])
        ['bob']
        >>> get_unresolved_handles(["alice"], [alice])
        []
        >>> get_unresolved_handles([], [alice])
        []
    """
    result = resolve_mentions(handles, patrons)
    return [u.handle for u in result.unresolved]


@deal.pre(
    lambda mentions_result: all(len(mention.handle) > 0 for mention in mentions_result.resolved)
)
@deal.ensure(lambda mentions_result, result: len(result) == len(mentions_result.resolved))
def get_resolved_patron_ids(mentions_result: MentionsResult) -> list[PatronId]:
    """Extract patron IDs from a MentionsResult.

    Args:
        mentions_result: The mentions resolution result.

    Returns:
        List of resolved patron IDs.

    Examples:
        >>> alice = PatronMatch(PatronId("p-001"), "alice", "Alice Bot")
        >>> result = resolve_mentions(["alice"], [alice])
        >>> pids = get_resolved_patron_ids(result)
        >>> len(pids)
        1
        >>> pids[0]
        'p-001'
    """
    return [resolved_mention.patron_id for resolved_mention in mentions_result.resolved]


@deal.pre(
    lambda mentions_result: all(
        len(unresolved_mention.handle) > 0 for unresolved_mention in mentions_result.unresolved
    )
)
@deal.ensure(lambda mentions_result, result: len(result) == len(mentions_result.unresolved))
@deal.post(lambda result: all(len(handle) > 0 for handle in result))
def get_unresolved_handles_from_result(mentions_result: MentionsResult) -> list[str]:
    """Extract unresolved handles from a MentionsResult.

    Args:
        mentions_result: The mentions resolution result.

    Returns:
        List of unresolved handles.

    Examples:
        >>> alice = PatronMatch(PatronId("p-001"), "alice", "Alice Bot")
        >>> result = resolve_mentions(["charlie"], [alice])
        >>> handles = get_unresolved_handles_from_result(result)
        >>> handles
        ['charlie']
    """
    return [unresolved_mention.handle for unresolved_mention in mentions_result.unresolved]


@deal.pre(
    lambda mentions_result: all(
        len(ambiguous_mention.handle) > 0 for ambiguous_mention in mentions_result.ambiguous
    )
)
@deal.ensure(lambda mentions_result, result: result == (len(mentions_result.ambiguous) > 0))
def has_ambiguous_mentions(mentions_result: MentionsResult) -> bool:
    """Check if there are any ambiguous mentions.

    Args:
        mentions_result: The mentions resolution result.

    Returns:
        True if any mentions are ambiguous.

    Examples:
        >>> alice = PatronMatch(PatronId("p-001"), "alice", "Alice Bot")
        >>> bob = PatronMatch(PatronId("p-002"), "bob", "Bob Bot")
        >>> result = resolve_mentions(["alice"], [alice, bob])
        >>> has_ambiguous_mentions(result)
        False
        >>> # Ambiguous: same display_name without unique alias
        >>> alice2 = PatronMatch(PatronId("p-002"), None, "Alice")  # No alias
        >>> result2 = resolve_mentions(["Alice"], [alice2])  # Match only by display_name, unique - not ambiguous
        >>> has_ambiguous_mentions(result2)
        False
    """
    return len(mentions_result.ambiguous) > 0
