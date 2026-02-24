"""Internal SVG sanitization helpers.

This module contains the internal implementation details for SVG sanitization.
It is intentionally kept private (not exported from the public API).

Security Model:
    - Allowlist-based: Only explicitly permitted elements/attributes pass through
    - Remove all script handlers (onclick, onload, onerror, etc.)
    - Remove external references (xlink:href to external URLs)
    - Preserve structural and styling attributes needed for diagram rendering

Canonical Policy Source:
    docs/adr-002-mermaid-svg-sanitization.md
"""

import re
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

# =============================================================================
# Patterns
# =============================================================================

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

# Pattern to match DOCTYPE declarations (can span multiple lines)
DOCTYPE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"<!DOCTYPE[^>]*\[.*?\]\s*>|<!DOCTYPE[^>]*>", re.IGNORECASE | re.DOTALL
)

# Pattern to match event handler attributes (quoted or unquoted)
EVENT_HANDLER_ATTR_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'\s+(on\w+)\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)', re.IGNORECASE
)

# Pattern to match href and xlink:href attributes
HREF_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'(?:xlink:)?href\s*=\s*(?:"([^"]*)"|\'([^\']*)\')', re.IGNORECASE
)

# Pattern to match attributes (name=value), excluding xmlns declarations
ATTR_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'\s+(?!xmlns(?::|$))(\w+(?::\w+)?)\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+)', re.IGNORECASE
)

# Pattern to match elements (self-closing and with content)
ELEMENT_PATTERN: Final[re.Pattern[str]] = re.compile(r"<(/?)(\w+)([^>]*?)(/?)>", re.DOTALL)


@deal.pre(lambda svg_content: svg_content is not None)
@deal.post(lambda result: isinstance(result, str))
def remove_doctype(svg_content: str) -> str:
    """Remove DOCTYPE declarations which can contain malicious entity definitions.

    DOCTYPE declarations in SVG can be used for XXE attacks and entity injection.
    They must be stripped entirely.

    Args:
        svg_content: SVG string to process.

    Returns:
        SVG string with DOCTYPE removed.

    Examples:
        >>> result = remove_doctype('<svg><!DOCTYPE svg PUBLIC "x"><rect/></svg>')
        >>> '<!DOCTYPE' not in result
        True
        >>> result = remove_doctype('<svg><rect/></svg>')
        >>> '<rect' in result
        True
    """
    return DOCTYPE_PATTERN.sub("", svg_content)


@deal.pre(lambda value: value is not None)
@deal.post(lambda result: isinstance(result, bool))
def is_external_url(value: str) -> bool:
    """Check if a value is an external URL (dangerous).

    Args:
        value: The attribute value to check.

    Returns:
        True if the value is an external URL.

    Examples:
        >>> is_external_url("https://evil.com")
        True
        >>> is_external_url("http://example.com")
        True
        >>> is_external_url("#internal-ref")
        False
        >>> is_external_url("")
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


@deal.pre(
    lambda content, tag_name, start_pos: (
        content is not None
        and tag_name is not None
        and len(tag_name) > 0
        and isinstance(start_pos, int)
        and start_pos >= 0
    )
)
@deal.post(lambda result: result is None or hasattr(result, "group"))
def find_closing_tag(content: str, tag_name: str, start_pos: int) -> re.Match[str] | None:
    """Find the matching closing tag for an opening tag.

    Handles nested elements of the same type.

    Args:
        content: The SVG content string.
        tag_name: The tag name to find closing tag for.
        start_pos: Position to start searching from.

    Returns:
        Match object for closing tag or None.

    Examples:
        >>> find_closing_tag("<svg><rect/></svg>", "svg", 5) is not None
        True
        >>> find_closing_tag("<svg><rect/></svg>", "rect", 0) is None
        True
    """
    depth = 1
    pos = start_pos
    # Escape tag_name to prevent regex injection from special characters
    escaped_tag = re.escape(tag_name)
    open_pattern = re.compile(rf"<{escaped_tag}[^>]*?(?<!/)>", re.IGNORECASE)
    close_pattern = re.compile(rf"</{escaped_tag}\s*>", re.IGNORECASE)

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
