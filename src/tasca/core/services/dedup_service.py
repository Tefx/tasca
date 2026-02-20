"""
Deduplication service - core business logic for content deduplication.

This module provides pure functions for computing content hashes and creating
previews for deduplication. All I/O operations are handled by the shell layer.

Deduplication Strategy:
    - Content is hashed using SHA-256
    - Hash is used as primary key in dedup table
    - Preview is truncated content for display purposes
    - return_existing: When duplicate detected, return existing record
"""

import hashlib

import deal


@deal.pre(lambda content: content is not None)
@deal.post(lambda result: len(result) == 64)  # SHA-256 hex digest is 64 chars
def compute_content_hash(content: str) -> str:
    """Compute SHA-256 hash of content for deduplication.

    The hash is used as the primary key in the dedup table, providing
    O(1) lookup for duplicate detection.

    Args:
        content: Content string to hash (non-empty).

    Returns:
        Hexadecimal SHA-256 hash (64 characters).

    Examples:
        >>> hash1 = compute_content_hash("Hello, world!")
        >>> len(hash1)
        64
        >>> hash1 == compute_content_hash("Hello, world!")  # Deterministic
        True
        >>> hash1 == compute_content_hash("Different content")  # Different
        False
        >>> compute_content_hash("")  # Empty string is valid
        'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@deal.pre(lambda content, max_length=200: content is not None and max_length > 0)
@deal.post(lambda result: isinstance(result, str))
def truncate_preview(content: str, max_length: int = 200) -> str:
    """Truncate content to preview length for display.

    Creates a human-readable preview suitable for display in dedup records.
    Truncates to max_length and appends "..." if content was truncated.

    Args:
        content: Content string to truncate.
        max_length: Maximum length of preview (default 200).

    Returns:
        Truncated preview string.

    Examples:
        >>> truncate_preview("Short")
        'Short'
        >>> result = truncate_preview("A" * 250)
        >>> len(result)
        203
        >>> result.endswith("...")
        True
        >>> result2 = truncate_preview("A" * 300, max_length=50)
        >>> len(result2)
        53
        >>> result2.endswith("...")
        True
        >>> truncate_preview("")
        ''
    """
    if len(content) <= max_length:
        return content
    return content[:max_length] + "..."


@deal.pre(lambda content, preview_max_length=200: content is not None and preview_max_length > 0)
@deal.post(lambda result: len(result[0]) == 64)
@deal.post(lambda result: isinstance(result[1], str))
def compute_hash_and_preview(content: str, preview_max_length: int = 200) -> tuple[str, str]:
    """Compute both hash and preview in a single call.

    Convenience function that combines hash computation and preview truncation.

    Args:
        content: Content string to process.
        preview_max_length: Maximum length of preview (default 200).

    Returns:
        Tuple of (content_hash, content_preview).

    Examples:
        >>> h, p = compute_hash_and_preview("Test content")
        >>> len(h)
        64
        >>> isinstance(p, str)
        True
        >>> h2, p2 = compute_hash_and_preview("Test content")
        >>> h == h2  # Same content = same hash
        True
    """
    content_hash = compute_content_hash(content)
    content_preview = truncate_preview(content, preview_max_length)
    return content_hash, content_preview
