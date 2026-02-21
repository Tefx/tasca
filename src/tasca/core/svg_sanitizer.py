"""
SVG Sanitizer for Mermaid diagram output - core security logic.

This module provides pure functions for sanitizing SVG content by allowing
only safe elements and attributes, removing potentially dangerous content
like scripts, event handlers, and external references.

Security Model:
    - Allowlist-based: Only explicitly permitted elements/attributes pass through
    - Remove all script handlers (onclick, onload, onerror, etc.)
    - Remove external references (xlink:href to external URLs)
    - Preserve structural and styling attributes needed for diagram rendering

Allowed Elements (for Mermaid diagrams):
    svg, g, path, circle, rect, line, polygon, text, tspan,
    title, desc, defs, marker, use, symbol

Allowed Attributes:
    class, id, transform, d, cx, cy, r, x, y, width, height,
    fill, stroke, style, viewBox, points
"""

import re
from dataclasses import dataclass, field
from typing import Final

import deal


# =============================================================================
# Constants - Allowlists
# =============================================================================

ALLOWED_ELEMENTS: Final[frozenset[str]] = frozenset(
    [
        # Root
        "svg",
        # Containers
        "g",
        "defs",
        "symbol",
        # Shapes
        "path",
        "circle",
        "rect",
        "line",
        "polygon",
        # Text
        "text",
        "tspan",
        # Metadata
        "title",
        "desc",
        # References
        "marker",
        "use",
    ]
)

ALLOWED_ATTRIBUTES: Final[frozenset[str]] = frozenset(
    [
        # Identification
        "class",
        "id",
        # Geometry
        "transform",
        "d",
        "cx",
        "cy",
        "r",
        "x",
        "y",
        "width",
        "height",
        "points",
        # Line attributes
        "x1",
        "y1",
        "x2",
        "y2",
        # Styling
        "fill",
        "stroke",
        "style",
        # Viewport
        "viewBox",
        # Marker references
        "marker-start",
        "marker-mid",
        "marker-end",
        "refX",
        "refY",
        "markerWidth",
        "markerHeight",
        "orient",
        # Reference (use element) - internal references only
        "href",  # Modern SVG uses href, not xlink:href (external refs filtered separately)
    ]
)

# Event handler pattern (all on* attributes are dangerous)
# Must be on followed by word characters only (not fake like onclickfake which has chars after)
EVENT_HANDLER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^on[a-z]+$", re.IGNORECASE)

# External URL pattern (http, https, ftp, etc.)
EXTERNAL_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:https?|ftp|data|javascript|vbscript):", re.IGNORECASE
)

# Data URI with potentially dangerous content
DANGEROUS_DATA_URI_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^data:(?:text/html|text/javascript|application/javascript|image/svg\+xml)", re.IGNORECASE
)


# =============================================================================
# Data Structures
# =============================================================================


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

    @property
    def is_clean(self) -> bool:
        """True if no dangerous content was removed (SVG was already safe)."""
        return (
            self.elements_removed == 0
            and self.attributes_removed == 0
            and self.event_handlers_removed == 0
            and self.external_refs_removed == 0
        )

    @property
    def total_removed(self) -> int:
        """Total count of all removed items."""
        return (
            self.elements_removed
            + self.attributes_removed
            + self.event_handlers_removed
            + self.external_refs_removed
        )


# =============================================================================
# Core Sanitization Functions
# =============================================================================


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
        >>> result.is_clean
        True
        >>> '<rect' in result.sanitized_svg
        True

        >>> result = sanitize_svg('<svg><script>alert(1)</script></svg>')
        >>> result.elements_removed
        1
        >>> '<script' in result.sanitized_svg
        False

        >>> result = sanitize_svg('<svg onclick="alert(1)"><circle/></svg>')
        >>> result.event_handlers_removed
        1
        >>> 'onclick' in result.sanitized_svg
        False
    """
    elements_removed = 0
    attributes_removed = 0
    event_handlers_removed = 0
    external_refs_removed = 0

    # Process the SVG content
    result_svg = svg_content

    # Step 0: Remove DOCTYPE declarations (can contain malicious entities)
    result_svg = _remove_doctype(result_svg)

    # Step 1: Remove disallowed elements (with their content)
    result_svg, elements_removed = _remove_disallowed_elements(result_svg)

    # Step 2: Remove event handlers (on* attributes)
    result_svg, event_handlers_removed = _remove_event_handlers(result_svg)

    # Step 3: Remove external references
    result_svg, external_refs_removed = _remove_external_references(result_svg)

    # Step 4: Remove disallowed attributes from remaining elements
    result_svg, attributes_removed = _remove_disallowed_attributes(result_svg)

    return SanitizationResult(
        sanitized_svg=result_svg,
        elements_removed=elements_removed,
        attributes_removed=attributes_removed,
        event_handlers_removed=event_handlers_removed,
        external_refs_removed=external_refs_removed,
    )


@deal.pre(lambda value: value is not None)
@deal.post(lambda result: isinstance(result, bool))
def is_event_handler_attribute(attr_name: str) -> bool:
    """Check if an attribute name is an event handler.

    Event handlers start with 'on' and are JavaScript execution vectors.
    Examples: onclick, onload, onerror, onmouseover, onfocus, etc.

    Args:
        attr_name: The attribute name to check.

    Returns:
        True if the attribute is an event handler.

    Examples:
        >>> is_event_handler_attribute("onclick")
        True
        >>> is_event_handler_attribute("onload")
        True
        >>> is_event_handler_attribute("ONERROR")  # Case insensitive
        True
        >>> is_event_handler_attribute("class")
        False
        >>> is_event_handler_attribute("transform")
        False
    """
    return bool(EVENT_HANDLER_PATTERN.match(attr_name))


@deal.pre(lambda value: value is not None)
@deal.post(lambda result: isinstance(result, bool))
def is_allowed_element(tag_name: str) -> bool:
    """Check if an element tag is in the allowlist.

    Args:
        tag_name: The element tag name (case-sensitive).

    Returns:
        True if the element is allowed.

    Examples:
        >>> is_allowed_element("svg")
        True
        >>> is_allowed_element("path")
        True
        >>> is_allowed_element("script")
        False
        >>> is_allowed_element("foreignObject")
        False
        >>> is_allowed_element("SCRIPT")  # Case-sensitive
        False
    """
    return tag_name in ALLOWED_ELEMENTS


@deal.pre(lambda value: value is not None)
@deal.post(lambda result: isinstance(result, bool))
def is_allowed_attribute(attr_name: str) -> bool:
    """Check if an attribute name is in the allowlist.

    Args:
        attr_name: The attribute name (case-sensitive).

    Returns:
        True if the attribute is allowed.

    Examples:
        >>> is_allowed_attribute("class")
        True
        >>> is_allowed_attribute("transform")
        True
        >>> is_allowed_attribute("onclick")
        False
        >>> is_allowed_attribute("xmlns:xlink")
        False
    """
    return attr_name in ALLOWED_ATTRIBUTES


@deal.pre(lambda value: value is not None)
@deal.post(lambda result: isinstance(result, bool))
def is_external_reference(value: str) -> bool:
    """Check if a value is an external reference (dangerous URL).

    External references can be used for:
    - Data exfiltration
    - XSS via javascript: URLs
    - SVG injection via data: URIs

    Args:
        value: The attribute value to check.

    Returns:
        True if the value is an external reference.

    Examples:
        >>> is_external_reference("https://evil.com/payload")
        True
        >>> is_external_reference("javascript:alert(1)")
        True
        >>> is_external_reference("data:text/html,<script>")
        True
        >>> is_external_reference("#marker")
        False
        >>> is_external_reference("")
        False
        >>> is_external_reference("none")
        False
    """
    if not value or not isinstance(value, str):
        return False

    value_stripped = value.strip()

    # Check for external URL schemes
    if EXTERNAL_URL_PATTERN.match(value_stripped):
        # Allow safe data URIs (images like data:image/png, data:image/gif)
        if value_stripped.lower().startswith("data:"):
            return bool(DANGEROUS_DATA_URI_PATTERN.match(value_stripped))
        return True

    return False


# =============================================================================
# Internal Helper Functions
# =============================================================================


def _remove_doctype(svg_content: str) -> str:
    """Remove DOCTYPE declarations which can contain malicious entity definitions.

    DOCTYPE declarations in SVG can be used for XXE attacks and entity injection.
    They must be stripped entirely.

    Args:
        svg_content: SVG string to process.

    Returns:
        SVG string with DOCTYPE removed.

    Examples:
        >>> svg = '<!DOCTYPE svg [<!ENTITY xss "alert">]><svg/>'
        >>> result = _remove_doctype(svg)
        >>> 'DOCTYPE' in result
        False
    """
    # Match DOCTYPE declarations (may span multiple lines)
    doctype_pattern = re.compile(
        r"<!DOCTYPE[^>]*\[.*?\]\s*>|<!DOCTYPE[^>]*>",
        re.IGNORECASE | re.DOTALL
    )
    return doctype_pattern.sub("", svg_content)


def _remove_disallowed_elements(svg_content: str) -> tuple[str, int]:
    """Remove elements that are not in the allowlist.

    Removes the entire element including its content.

    Args:
        svg_content: SVG string to process.

    Returns:
        Tuple of (cleaned SVG, count of elements removed).

    Examples:
        >>> svg = '<svg><script>alert(1)</script><rect/></svg>'
        >>> result, count = _remove_disallowed_elements(svg)
        >>> count
        1
        >>> '<script' in result
        False
    """
    removed = 0
    result = svg_content

    # Pattern to match elements (self-closing and with content)
    # Matches: <tag .../> or <tag ...>...</tag>
    element_pattern = re.compile(r"<(/?)(\w+)([^>]*?)(/?)>", re.DOTALL)

    # Track which elements to remove
    elements_to_remove: list[tuple[str, int, int]] = []
    last_result = None

    # Keep iterating until no more changes
    while last_result != result:
        last_result = result
        elements_to_remove = []

        for match in element_pattern.finditer(result):
            is_closing = match.group(1) == "/"
            tag_name = match.group(2)
            is_self_closing = match.group(4) == "/"

            if not is_allowed_element(tag_name) and not is_closing:
                # For non-self-closing tags, we need to find the matching end tag
                if not is_self_closing:
                    # Find matching closing tag
                    start_pos = match.end()
                    end_match = _find_closing_tag(result, tag_name, start_pos)
                    if end_match:
                        elements_to_remove.append((tag_name, match.start(), end_match.end()))
                    else:
                        # No closing tag found, remove just the opening tag
                        elements_to_remove.append((tag_name, match.start(), match.end()))
                else:
                    # Self-closing tag
                    elements_to_remove.append((tag_name, match.start(), match.end()))

        # Remove elements (from end to start to preserve indices)
        for tag_name, start, end in reversed(elements_to_remove):
            result = result[:start] + result[end:]
            removed += 1

    return result, removed


def _find_closing_tag(content: str, tag_name: str, start_pos: int) -> re.Match[str] | None:
    """Find the matching closing tag for an opening tag.

    Handles nested elements of the same type.

    Args:
        content: The SVG content string.
        tag_name: The tag name to find closing tag for.
        start_pos: Position to start searching from.

    Returns:
        Match object for closing tag or None.
    """
    depth = 1
    pos = start_pos
    open_pattern = re.compile(rf"<{tag_name}[^>]*?(?<!/)>", re.IGNORECASE)
    close_pattern = re.compile(rf"</{tag_name}\s*>", re.IGNORECASE)

    while pos < len(content) and depth > 0:
        next_open = open_pattern.search(content, pos)
        next_close = close_pattern.search(content, pos)

        if next_close is None:
            return None

        if next_open and next_open.start() < next_close.start():
            depth += 1
            pos = next_open.end()
        else:
            depth -= 1
            if depth == 0:
                return next_close
            pos = next_close.end()

    return None


def _remove_event_handlers(svg_content: str) -> tuple[str, int]:
    """Remove event handler attributes (on* attributes).

    Args:
        svg_content: SVG string to process.

    Returns:
        Tuple of (cleaned SVG, count of handlers removed).

    Examples:
        >>> svg = '<svg onclick="alert(1)"><rect onload="x"/></svg>'
        >>> result, count = _remove_event_handlers(svg)
        >>> count
        2
        >>> 'onclick' in result
        False
    """
    removed = 0
    result = svg_content

    # Pattern to match event handler attributes
    # Captures: attribute name and its value (quoted or unquoted)
    pattern = re.compile(r'\s+(on\w+)\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)', re.IGNORECASE)

    # Keep finding and removing until clean
    last_result = None
    while last_result != result:
        last_result = result
        matches = list(pattern.finditer(result))
        for match in reversed(matches):
            result = result[: match.start()] + result[match.end() :]
            removed += 1

    return result, removed


def _remove_external_references(svg_content: str) -> tuple[str, int]:
    """Remove external references from attribute values.

    Handles href, xlink:href, and any attribute containing external URLs.

    Args:
        svg_content: SVG string to process.

    Returns:
        Tuple of (cleaned SVG, count of refs removed).
    """
    removed = 0
    result = svg_content

    # Pattern to match href and xlink:href attributes
    href_pattern = re.compile(r'(?:xlink:)?href\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', re.IGNORECASE)

    # Keep finding and removing until clean
    last_result = None
    while last_result != result:
        last_result = result
        matches = list(href_pattern.finditer(result))
        for match in reversed(matches):
            href_value = match.group(1) or match.group(2)
            if href_value and is_external_reference(href_value):
                result = result[: match.start()] + result[match.end() :]
                removed += 1

    return result, removed


def _remove_disallowed_attributes(svg_content: str) -> tuple[str, int]:
    """Remove attributes that are not in the allowlist.

    This is applied after event handlers and external refs have been removed.

    Args:
        svg_content: SVG string to process.

    Returns:
        Tuple of (cleaned SVG, count of attributes removed).
    """
    removed = 0
    result = svg_content

    # Pattern to match attributes (name=value)
    # Excludes xmlns declarations which we may want to keep for valid SVG
    attr_pattern = re.compile(
        r'\s+(?!xmlns(?::|$))(\w+(?::\w+)?)\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)', re.IGNORECASE
    )

    # Keep finding and removing until clean
    last_result = None
    while last_result != result:
        last_result = result
        matches = list(attr_pattern.finditer(result))
        for match in reversed(matches):
            attr_name = match.group(1)
            # Skip allowed attributes and event handlers (already removed)
            if not is_allowed_attribute(attr_name) and not is_event_handler_attribute(attr_name):
                result = result[: match.start()] + result[match.end() :]
                removed += 1

    return result, removed


# =============================================================================
# Convenience Functions
# =============================================================================


@deal.pre(lambda svg_content: svg_content is not None)
@deal.post(lambda result: isinstance(result, str))
def sanitize_svg_content(svg_content: str) -> str:
    """Simple sanitization that returns only the cleaned SVG string.

    Convenience function when you don't need statistics.

    Args:
        svg_content: The raw SVG string to sanitize.

    Returns:
        The sanitized SVG string.

    Examples:
        >>> clean = sanitize_svg_content('<svg><script>x</script></svg>')
        >>> '<script' in clean
        False
        >>> '<svg' in clean
        True
    """
    result = sanitize_svg(svg_content)
    return result.sanitized_svg
