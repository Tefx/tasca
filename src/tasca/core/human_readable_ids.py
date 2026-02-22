"""
Human-readable ID generation for tables.

This module generates memorable, human-readable identifiers instead of UUIDs.
Examples: "clever-fox-jumps-123", "brave-panda-dances-456"

Design:
- Core module: Pure logic, no I/O, randomness injected as parameter
- Word lists imported from _word_lists module
- Format: adjective-noun-verb[-suffix]

Usage in shell layer:
    import random
    from tasca.core.human_readable_ids import generate_human_readable_id

    def make_random_choice(words: list[str]) -> str:
        return random.choice(words)

    table_id = generate_human_readable_id(
        random_choice=make_random_choice,
        suffix=123
    )
"""

from typing import Callable

import deal

from tasca.core._word_lists import ADJECTIVES, NOUNS, VERBS


# =============================================================================
# Helper Functions
# =============================================================================


@deal.pre(
    lambda words, random_choice: (
        len(words) > 0
        and all(w.isalpha() and w.islower() for w in words)
        and callable(random_choice)
    )
)
def pick_random_word(
    words: list[str] | tuple[str, ...], random_choice: Callable[[list[str]], str]
) -> str:
    """Pick a random word from a word list.

    The random_choice function is injected for testability and purity.
    In production, pass random.choice; in tests, pass a deterministic function.

    Args:
        words: List or tuple of words to choose from.
        random_choice: Function that picks one word from a list.

    Returns:
        A single word from the list.

    Examples:
        >>> pick_random_word(["a", "b"], lambda lst: lst[0])
        'a'
        >>> pick_random_word(["x", "y", "z"], lambda lst: lst[2])
        'z'
        >>> pick_random_word(("one", "two"), lambda lst: lst[1])
        'two'
    """
    # Convert tuple to list for the random_choice function
    word_list = list(words) if isinstance(words, tuple) else words
    return random_choice(word_list)


@deal.pre(
    lambda adjective, noun, verb, separator="-", suffix=None: (
        len(adjective) > 0 and len(noun) > 0 and len(verb) > 0 and len(separator) > 0
    )
)
@deal.post(lambda result: len(result) > 0)
def format_human_readable_id(
    adjective: str,
    noun: str,
    verb: str,
    separator: str = "-",
    suffix: int | None = None,
) -> str:
    """Format a human-readable ID from word components.

    Args:
        adjective: The adjective word (e.g., "quick").
        noun: The noun word (e.g., "fox").
        verb: The verb word (e.g., "jumps").
        separator: Separator between words (default "-").
        suffix: Optional numeric suffix for uniqueness.

    Returns:
        Formatted ID like "quick-fox-jumps" or "quick-fox-jumps-123".

    Examples:
        >>> format_human_readable_id("quick", "fox", "jumps")
        'quick-fox-jumps'
        >>> format_human_readable_id("brave", "panda", "dances", suffix=42)
        'brave-panda-dances-42'
        >>> format_human_readable_id("clever", "owl", "flies", separator="_")
        'clever_owl_flies'
        >>> format_human_readable_id("wise", "wolf", "runs", separator="_", suffix=99)
        'wise_wolf_runs_99'
    """
    base = separator.join([adjective, noun, verb])
    if suffix is not None:
        return f"{base}{separator}{suffix}"
    return base


# =============================================================================
# Main Generation Function
# =============================================================================


@deal.pre(
    lambda random_choice, separator="-", suffix=None: callable(random_choice) and len(separator) > 0
)
def generate_human_readable_id(
    random_choice: Callable[[list[str]], str],
    separator: str = "-",
    suffix: int | None = None,
) -> str:
    """Generate a human-readable ID from random words.

    This is the main entry point for ID generation. The random_choice
    function is injected to keep this module pure (no random import).

    Format: adjective-noun-verb[-suffix]
    Example: "clever-fox-jumps-123"

    Args:
        random_choice: Function that picks one word from a list.
            In production: random.choice
            In tests: lambda lst: lst[i] for deterministic output
        separator: Separator between words (default "-").
        suffix: Optional numeric suffix for uniqueness.

    Returns:
        A human-readable ID string.

    Examples:
        >>> deterministic = lambda lst: lst[0]
        >>> generate_human_readable_id(deterministic)
        'able-ant-dances'
        >>> generate_human_readable_id(deterministic, suffix=1)
        'able-ant-dances-1'
        >>> generate_human_readable_id(deterministic, separator="_")
        'able_ant_dances'
        >>> generate_human_readable_id(lambda lst: lst[-1], suffix=999)
        'witty-wind-waves-999'

    Note:
        For uniqueness guarantees, the caller (shell layer) should:
        1. Generate an ID without suffix
        2. Check if it exists in storage
        3. If collision, retry with suffix (1, 2, 3, ...)
    """
    adjective = pick_random_word(ADJECTIVES, random_choice)
    noun = pick_random_word(NOUNS, random_choice)
    verb = pick_random_word(VERBS, random_choice)

    return format_human_readable_id(
        adjective=adjective,
        noun=noun,
        verb=verb,
        separator=separator,
        suffix=suffix,
    )


# =============================================================================
# Validation Functions
# =============================================================================


@deal.post(lambda result: isinstance(result, bool))
def is_human_readable_id(id_string: str, separator: str = "-") -> bool:
    """Check if a string matches the human-readable ID format.

    Does NOT validate that words are from our word lists,
    only checks the structure.

    Args:
        id_string: The string to check.
        separator: Expected separator (default "-").

    Returns:
        True if the string matches the expected format.

    Examples:
        >>> is_human_readable_id("quick-fox-jumps")
        True
        >>> is_human_readable_id("quick-fox-jumps-123")
        True
        >>> is_human_readable_id("quick_fox_jumps", separator="_")
        True
        >>> is_human_readable_id("just-two")
        False
        >>> is_human_readable_id("")
        False
    """
    if not id_string:
        return False

    # Check for spaces or invalid characters
    if " " in id_string or "\t" in id_string:
        return False

    parts = id_string.split(separator)

    # Must have at least 3 parts (adj-noun-verb) or 4 with suffix
    if len(parts) < 3 or len(parts) > 4:
        return False

    # If 4 parts, last must be numeric suffix
    if len(parts) == 4:
        if not parts[3].isdigit():
            return False
        # Check that last part is numeric (already validated above)
        words_to_check = parts[:3]
    else:
        words_to_check = parts

    # Each word part must be lowercase alphanumeric
    for part in words_to_check:
        if not part:
            return False
        if not part.isalnum() or not part.islower():
            return False

    return True


@deal.post(lambda result: isinstance(result, bool))
def is_valid_word(word: str, word_list: tuple[str, ...]) -> bool:
    """Check if a word is in the given word list.

    Args:
        word: The word to check.
        word_list: The word list to check against.

    Returns:
        True if the word is in the list.

    Examples:
        >>> is_valid_word("quick", ADJECTIVES)
        True
        >>> is_valid_word("fox", NOUNS)
        True
        >>> is_valid_word("jumps", VERBS)
        True
        >>> is_valid_word("notaword", ADJECTIVES)
        False
    """
    return word in word_list


# =============================================================================
# ID Parsing Functions
# =============================================================================


@deal.post(lambda result: result is None or (isinstance(result, tuple) and len(result) == 4))
def parse_human_readable_id(
    id_string: str,
    separator: str = "-",
) -> tuple[str, str, str, int | None] | None:
    """Parse a human-readable ID into its components.

    Args:
        id_string: The ID string to parse.
        separator: Expected separator (default "-").

    Returns:
        Tuple of (adjective, noun, verb, suffix) if valid, None otherwise.
        Suffix is None if no suffix present.

    Examples:
        >>> parse_human_readable_id("quick-fox-jumps")
        ('quick', 'fox', 'jumps', None)
        >>> parse_human_readable_id("quick-fox-jumps-123")
        ('quick', 'fox', 'jumps', 123)
        >>> parse_human_readable_id("quick_fox_jumps", separator="_")
        ('quick', 'fox', 'jumps', None)
        >>> parse_human_readable_id("invalid") is None
        True
        >>> parse_human_readable_id("too-many-parts-here-now") is None
        True
    """
    parts = id_string.split(separator)

    # Must have 3 or 4 parts
    if len(parts) not in (3, 4):
        return None

    if len(parts) == 3:
        # All parts must be lowercase alphanumeric
        for part in parts:
            if not part or not part.isalnum() or not part.islower():
                return None
        return (parts[0], parts[1], parts[2], None)

    # 4 parts: last must be numeric
    if not parts[3].isdigit():
        return None

    # First 3 parts must be lowercase alphanumeric
    for part in parts[:3]:
        if not part or not part.isalnum() or not part.islower():
            return None

    return (parts[0], parts[1], parts[2], int(parts[3]))


# =============================================================================
# Statistics Functions
# =============================================================================


@deal.post(lambda result: isinstance(result, int) and result > 0)
def calculate_total_combinations() -> int:
    """Calculate the total number of unique ID combinations (without suffix).

    Returns:
        Total number of unique adjective-noun-verb combinations.

    Examples:
        >>> total = calculate_total_combinations()
        >>> total == len(ADJECTIVES) * len(NOUNS) * len(VERBS)
        True
        >>> total > 1000  # We should have thousands of combinations
        True
    """
    return len(ADJECTIVES) * len(NOUNS) * len(VERBS)


@deal.post(lambda result: isinstance(result, int) and result > 0)
def get_unique_word_count() -> int:
    """Get the count of unique words across all lists.

    Returns:
        Total number of unique words in all lists combined.

    Examples:
        >>> count = get_unique_word_count()
        >>> count == len(ADJECTIVES) + len(NOUNS) + len(VERBS)
        True
    """
    return len(ADJECTIVES) + len(NOUNS) + len(VERBS)
