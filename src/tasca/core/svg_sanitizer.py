"""
SVG Sanitizer for Mermaid diagram output - core security logic.

Canonical Policy Source:
    This implementation follows the security policy defined in:
        docs/adr-002-mermaid-svg-sanitization.md

    Any changes to allowed elements, attributes, or forbids MUST be reflected
    in ADR-002 first. This file is the Python implementation of that policy.

    See the companion TypeScript implementation:
        web/src/rendering/svg-sanitizer.ts

This module provides pure functions for sanitizing SVG content by allowing
only safe elements and attributes, removing potentially dangerous content
like scripts, event handlers, and external references.

Security Model:
    - Allowlist-based: Only explicitly permitted elements/attributes pass through
    - Remove all script handlers (onclick, onload, onerror, etc.)
    - Remove external references (xlink:href to external URLs)
    - Preserve structural and styling attributes needed for diagram rendering
"""

import re
from dataclasses import dataclass
from typing import Final

import deal

from tasca.core.svg_sanitizer_internal import (
    ALLOWED_ATTRIBUTES,
    ALLOWED_ELEMENTS,
    ATTR_PATTERN,
    ELEMENT_PATTERN,
    EVENT_HANDLER_ATTR_PATTERN,
    HREF_PATTERN,
    find_closing_tag,
    is_external_url,
    remove_doctype,
)

# Event handler pattern (all on* attributes are dangerous)
EVENT_HANDLER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^on[a-z]+$", re.IGNORECASE)


@dataclass(frozen=True)
class SanitizationResult:
    """Result of SVG sanitization with details about removed content.

    Attributes:
        sanitized_svg: The cleaned SVG string.
        elements_removed: Count of disallowed elements removed.
        attributes_removed: Count of disallowed attributes removed.
        event_handlers_removed: Count of event handler attributes removed.
        external_refs_removed: Count of external references removed.
    """

    sanitized_svg: str
    elements_removed: int = 0
    attributes_removed: int = 0
    event_handlers_removed: int = 0
    external_refs_removed: int = 0

    @deal.post(lambda result: isinstance(result, bool))
    def is_clean(self) -> bool:
        """True if no dangerous content was removed (SVG was already safe).

        Examples:
            >>> result = SanitizationResult('<svg/>', 0, 0, 0, 0)
            >>> result.is_clean()
            True
            >>> result = SanitizationResult('<svg/>', 1, 0, 0, 0)
            >>> result.is_clean()
            False
        """
        return (
            self.elements_removed == 0
            and self.attributes_removed == 0
            and self.event_handlers_removed == 0
            and self.external_refs_removed == 0
        )

    @deal.post(lambda result: isinstance(result, int))
    @deal.post(lambda result: result >= 0)
    def total_removed(self) -> int:
        """Total count of all removed items.

        Examples:
            >>> result = SanitizationResult('<svg/>', 1, 2, 3, 4)
            >>> result.total_removed()
            10
            >>> result = SanitizationResult('<svg/>', 0, 0, 0, 0)
            >>> result.total_removed()
            0
        """
        return (
            self.elements_removed
            + self.attributes_removed
            + self.event_handlers_removed
            + self.external_refs_removed
        )


@deal.pre(lambda svg_content: svg_content is not None)
@deal.post(lambda result: isinstance(result, SanitizationResult))
@deal.post(lambda result: result.sanitized_svg is not None)
def sanitize_svg(svg_content: str) -> SanitizationResult:
    """Sanitize SVG content by removing dangerous elements and attributes.

    This function strips all elements and attributes not in the allowlist,
    removes event handlers, and neutralizes external references.

    Args:
        svg_content: The raw SVG string to sanitize.

    Returns:
        SanitizationResult containing the cleaned SVG and removal statistics.

    Examples:
        >>> result = sanitize_svg('<svg><rect x="10" y="10"/></svg>')
        >>> result.is_clean()
        True
        >>> '<rect' in result.sanitized_svg
        True

        >>> result = sanitize_svg('<svg><script>alert(1)</script></svg>')
        >>> result.elements_removed
        1
        >>> '<script' in result.sanitized_svg
        False
    """
    elements_removed = 0
    attributes_removed = 0
    event_handlers_removed = 0
    external_refs_removed = 0

    result_svg = svg_content

    # Step 0: Remove DOCTYPE declarations
    result_svg = remove_doctype(result_svg)

    # Step 1: Remove disallowed elements
    result_svg, elements_removed = _remove_disallowed_elements(result_svg)

    # Step 2: Remove event handlers
    result_svg, event_handlers_removed = _remove_event_handlers(result_svg)

    # Step 3: Remove external references
    result_svg, external_refs_removed = _remove_external_references(result_svg)

    # Step 4: Remove disallowed attributes
    result_svg, attributes_removed = _remove_disallowed_attributes(result_svg)

    return SanitizationResult(
        sanitized_svg=result_svg,
        elements_removed=elements_removed,
        attributes_removed=attributes_removed,
        event_handlers_removed=event_handlers_removed,
        external_refs_removed=external_refs_removed,
    )


@deal.pre(lambda attr_name: attr_name is not None)
@deal.post(lambda result: isinstance(result, bool))
def is_event_handler_attribute(attr_name: str) -> bool:
    """Check if an attribute name is an event handler.

    Examples:
        >>> is_event_handler_attribute("onclick")
        True
        >>> is_event_handler_attribute("class")
        False
    """
    return bool(EVENT_HANDLER_PATTERN.match(attr_name))


@deal.pre(lambda tag_name: tag_name is not None)
@deal.post(lambda result: isinstance(result, bool))
def is_allowed_element(tag_name: str) -> bool:
    """Check if an element tag is in the allowlist."""
    return tag_name in ALLOWED_ELEMENTS


@deal.pre(lambda attr_name: attr_name is not None)
@deal.post(lambda result: isinstance(result, bool))
def is_allowed_attribute(attr_name: str) -> bool:
    """Check if an attribute name is in the allowlist."""
    return attr_name in ALLOWED_ATTRIBUTES


@deal.pre(lambda value: value is not None)
@deal.post(lambda result: isinstance(result, bool))
def is_external_reference(value: str) -> bool:
    """Check if a value is an external reference (dangerous URL)."""
    return is_external_url(value)


@deal.pre(lambda svg_content: svg_content is not None)
@deal.post(lambda result: isinstance(result, tuple) and len(result) == 2)
@deal.post(lambda result: isinstance(result[0], str))
@deal.post(lambda result: isinstance(result[1], int) and result[1] >= 0)
def _remove_disallowed_elements(svg_content: str) -> tuple[str, int]:
    """Remove elements that are not in the allowlist."""
    removed = 0
    result = svg_content
    elements_to_remove: list[tuple[str, int, int]] = []
    last_result = None

    while last_result != result:
        last_result = result
        elements_to_remove = []

        for match in ELEMENT_PATTERN.finditer(result):
            is_closing = match.group(1) == "/"
            tag_name = match.group(2)
            is_self_closing = match.group(4) == "/"

            if not is_allowed_element(tag_name) and not is_closing:
                if not is_self_closing:
                    start_pos = match.end()
                    end_match = find_closing_tag(result, tag_name, start_pos)
                    if end_match:
                        elements_to_remove.append((tag_name, match.start(), end_match.end()))
                    else:
                        elements_to_remove.append((tag_name, match.start(), match.end()))
                else:
                    elements_to_remove.append((tag_name, match.start(), match.end()))

        for _tag_name, start, end in reversed(elements_to_remove):
            result = result[:start] + result[end:]
            removed += 1

    return result, removed


@deal.pre(lambda svg_content: svg_content is not None)
@deal.post(lambda result: isinstance(result, tuple) and len(result) == 2)
@deal.post(lambda result: isinstance(result[0], str))
@deal.post(lambda result: isinstance(result[1], int) and result[1] >= 0)
def _remove_event_handlers(svg_content: str) -> tuple[str, int]:
    """Remove event handler attributes (on* attributes)."""
    removed = 0
    result = svg_content
    last_result = None

    while last_result != result:
        last_result = result
        matches = list(EVENT_HANDLER_ATTR_PATTERN.finditer(result))
        for match in reversed(matches):
            result = result[: match.start()] + result[match.end() :]
            removed += 1

    return result, removed


@deal.pre(lambda svg_content: svg_content is not None)
@deal.post(lambda result: isinstance(result, tuple) and len(result) == 2)
@deal.post(lambda result: isinstance(result[0], str))
@deal.post(lambda result: isinstance(result[1], int) and result[1] >= 0)
def _remove_external_references(svg_content: str) -> tuple[str, int]:
    """Remove external references from attribute values."""
    removed = 0
    result = svg_content
    last_result = None

    while last_result != result:
        last_result = result
        matches = list(HREF_PATTERN.finditer(result))
        for match in reversed(matches):
            href_value = match.group(1) or match.group(2)
            if href_value and is_external_reference(href_value):
                result = result[: match.start()] + result[match.end() :]
                removed += 1

    return result, removed


@deal.pre(lambda svg_content: svg_content is not None)
@deal.post(lambda result: isinstance(result, tuple) and len(result) == 2)
@deal.post(lambda result: isinstance(result[0], str))
@deal.post(lambda result: isinstance(result[1], int) and result[1] >= 0)
def _remove_disallowed_attributes(svg_content: str) -> tuple[str, int]:
    """Remove attributes that are not in the allowlist."""
    removed = 0
    result = svg_content
    last_result = None

    while last_result != result:
        last_result = result
        matches = list(ATTR_PATTERN.finditer(result))
        for match in reversed(matches):
            attr_name = match.group(1)
            if not is_allowed_attribute(attr_name) and not is_event_handler_attribute(attr_name):
                result = result[: match.start()] + result[match.end() :]
                removed += 1

    return result, removed


@deal.pre(lambda svg_content: svg_content is not None)
@deal.post(lambda result: isinstance(result, str))
def sanitize_svg_content(svg_content: str) -> str:
    """Simple sanitization that returns only the cleaned SVG string.

    Examples:
        >>> clean = sanitize_svg_content('<svg><script>x</script></svg>')
        >>> '<script' in clean
        False
    """
    result = sanitize_svg(svg_content)
    return result.sanitized_svg
