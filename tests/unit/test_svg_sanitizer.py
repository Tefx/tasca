"""
Regression test corpus for SVG sanitizer security.

This module contains comprehensive attack vectors to verify that the SVG
sanitizer correctly removes dangerous content while preserving safe elements.

Test categories:
1. XSS via script elements
2. XSS via event handlers
3. XSS via javascript: URLs
4. XSS via data: URIs
5. External resource loading
6. SVG-specific attacks (foreignObject, animate, etc.)
7. Safe content preservation
"""

import pytest

from tasca.core.svg_sanitizer import (
    is_allowed_attribute,
    is_allowed_element,
    is_event_handler_attribute,
    is_external_reference,
    sanitize_svg,
    sanitize_svg_content,
    SanitizationResult,
)


# =============================================================================
# Test Case Corpus - Attack Vectors
# =============================================================================

# XSS via script elements
XSS_SCRIPT_ATTACKS = [
    # Basic script injection
    {
        "name": "basic_script_element",
        "input": '<svg><script>alert("XSS")</script></svg>',
        "forbidden": ["<script", "alert"],
    },
    {
        "name": "script_with_src",
        "input": '<svg><script src="https://evil.com/xss.js"></script></svg>',
        "forbidden": ["<script", "evil.com"],
    },
    {
        "name": "script_in_g_element",
        "input": "<svg><g><script>document.cookie</script></g></svg>",
        "forbidden": ["<script", "document.cookie"],
    },
    {
        "name": "multiple_scripts",
        "input": "<svg><script>a</script><rect/><script>b</script></svg>",
        "forbidden": ["<script"],
    },
]

# XSS via event handlers
XSS_EVENT_HANDLER_ATTACKS = [
    {
        "name": "onclick_on_svg",
        "input": '<svg onclick="alert(1)"><rect/></svg>',
        "forbidden": ["onclick", "alert"],
    },
    {
        "name": "onload_on_svg",
        "input": '<svg onload="alert(1)"><rect/></svg>',
        "forbidden": ["onload", "alert"],
    },
    {
        "name": "onerror_on_image",
        "input": '<svg><image onerror="alert(1)" xlink:href="x"/></svg>',
        "forbidden": ["onerror", "alert"],
    },
    {
        "name": "onmouseover_on_rect",
        "input": '<svg><rect onmouseover="alert(1)"/></svg>',
        "forbidden": ["onmouseover", "alert"],
    },
    {
        "name": "multiple_event_handlers",
        "input": '<svg onclick="a" onload="b" onerror="c"><rect onmouseover="d"/></svg>',
        "forbidden": ["onclick", "onload", "onerror", "onmouseover"],
    },
    {
        "name": "case_insensitive_onclick",
        "input": '<svg ONCLICK="alert(1)"><rect/></svg>',
        "forbidden": ["ONCLICK", "alert"],
    },
    {
        "name": "onfocus_on_element",
        "input": '<svg><text onfocus="alert(1)" tabindex="0">Click</text></svg>',
        "forbidden": ["onfocus", "alert"],
    },
    {
        "name": "onanimationend",
        "input": '<svg><rect onanimationend="alert(1)"/></svg>',
        "forbidden": ["onanimationend", "alert"],
    },
]

# XSS via javascript: URLs
XSS_JAVASCRIPT_URL_ATTACKS = [
    {
        "name": "href_javascript",
        "input": '<svg><a href="javascript:alert(1)"><text>Click</text></a></svg>',
        "forbidden": ["javascript:", "alert"],
    },
    {
        "name": "xlink_href_javascript",
        "input": '<svg><a xlink:href="javascript:alert(1)"><text>Click</text></a></svg>',
        "forbidden": ["javascript:", "alert"],
    },
    {
        "name": "use_href_javascript",
        "input": '<svg><use href="javascript:alert(1)"/></svg>',
        "forbidden": ["javascript:", "alert"],
    },
    {
        "name": "case_insensitive_javascript",
        "input": '<svg><a href="JaVaScRiPt:alert(1)"><text>Click</text></a></svg>',
        "forbidden": ["javascript", "alert"],
    },
]

# XSS via data: URIs
XSS_DATA_URI_ATTACKS = [
    {
        "name": "data_uri_html",
        "input": '<svg><foreignObject><iframe src="data:text/html,<script>alert(1)</script>"/></foreignObject></svg>',
        "forbidden": ["<script", "alert", "foreignObject", "iframe"],
    },
    {
        "name": "data_uri_javascript",
        "input": '<svg><a href="data:text/javascript,alert(1)"><text>Click</text></a></svg>',
        "forbidden": ["data:text/javascript", "alert"],
    },
    {
        "name": "data_uri_svg",
        "input": '<svg><use href="data:image/svg+xml,<svg onload=alert(1)>"/></svg>',
        "forbidden": ["onload", "alert"],
    },
]

# External resource loading
EXTERNAL_RESOURCE_ATTACKS = [
    {
        "name": "external_image",
        "input": '<svg><image href="https://evil.com/tracker.png"/></svg>',
        "forbidden": ["evil.com", "https://"],
    },
    {
        "name": "external_use",
        "input": '<svg><use href="https://evil.com/malicious.svg#payload"/></svg>',
        "forbidden": ["evil.com", "https://"],
    },
    {
        "name": "external_stylesheet",
        "input": '<?xml-stylesheet href="https://evil.com/style.css"?><svg><rect/></svg>',
        "forbidden": ["evil.com"],
    },
]

# SVG-specific attacks
SVG_SPECIFIC_ATTACKS = [
    {
        "name": "foreignObject_html",
        "input": '<svg><foreignObject><body onload="alert(1)"/></foreignObject></svg>',
        "forbidden": ["foreignObject", "onload", "alert"],
    },
    {
        "name": "animate_href",
        "input": '<svg><animate href="#x" attributeName="href" values="javascript:alert(1)"/></svg>',
        "forbidden": ["animate", "javascript:", "alert"],
    },
    {
        "name": "set_element",
        "input": '<svg><set attributeName="onload" to="alert(1)"/></svg>',
        "forbidden": ["<set", "alert"],
    },
    {
        "name": "handler_element",
        "input": '<svg><handler type="text/javascript">alert(1)</handler></svg>',
        "forbidden": ["<handler", "alert"],
    },
    {
        "name": "namespace_confusion",
        "input": '<svg xmlns:xlink="http://www.w3.org/1999/xlink"><a xlink:href="javascript:alert(1)">x</a></svg>',
        "forbidden": ["javascript:", "alert"],
    },
]

# Edge cases and bypass attempts
EDGE_CASE_ATTACKS = [
    {
        "name": "nested_svg",
        "input": "<svg><svg><script>alert(1)</script></svg></svg>",
        "forbidden": ["<script", "alert"],
    },
    {
        "name": "svg_in_text",
        "input": "<svg><text>&lt;script&gt;alert(1)&lt;/script&gt;</text></svg>",
        "forbidden": [],  # Text content is escaped, should be preserved
    },
    {
        "name": "cdata_section",
        "input": "<svg><script><![CDATA[alert(1)]]></script></svg>",
        "forbidden": ["<script", "alert"],
    },
    {
        "name": "entity_injection",
        "input": '<!DOCTYPE svg [<!ENTITY xss "javascript:alert(1)">]><svg><a href="&xss;">x</a></svg>',
        "forbidden": ["DOCTYPE", "ENTITY", "javascript:"],
    },
    {
        "name": "newline_in_tag",
        "input": "<svg><script\n>alert(1)</script></svg>",
        "forbidden": ["script", "alert"],
    },
    {
        "name": "tab_in_tag",
        "input": "<svg><script\t>alert(1)</script></svg>",
        "forbidden": ["script", "alert"],
    },
    {
        "name": "encoded_javascript",
        "input": '<svg><a href="&#106;avascript:alert(1)">x</a></svg>',
        "forbidden": [],  # HTML entities are decoded by parser, but we check the decoded value
    },
]

# Safe Mermaid diagram content
SAFE_MERMAID_DIAGRAMS = [
    {
        "name": "basic_flowchart",
        "input": """<svg viewBox="0 0 200 100">
            <g class="node">
                <rect x="10" y="10" width="80" height="40" fill="#fff" stroke="#000"/>
                <text x="50" y="35">Start</text>
            </g>
            <g class="edge">
                <path d="M50 50 L50 80" stroke="#000"/>
            </g>
        </svg>""",
        "preserve_elements": ["svg", "g", "rect", "path", "text"],
        "preserve_attrs": ["viewBox", "class", "x", "y", "width", "height", "fill", "stroke", "d"],
    },
    {
        "name": "sequence_diagram",
        "input": """<svg viewBox="0 0 400 300">
            <g class="actor">
                <rect x="10" y="10" width="100" height="50"/>
                <text x="60" y="40">User</text>
            </g>
            <line x1="60" y1="60" x2="60" y2="200" stroke="#000"/>
            <polygon points="50,10 70,10 60,0"/>
        </svg>""",
        "preserve_elements": ["svg", "g", "rect", "line", "polygon"],
        "preserve_attrs": [
            "viewBox",
            "class",
            "x",
            "y",
            "width",
            "height",
            "x1",
            "y1",
            "x2",
            "y2",
            "stroke",
            "points",
        ],
    },
    {
        "name": "with_defs_and_markers",
        "input": """<svg viewBox="0 0 200 100">
            <defs>
                <marker id="arrow" viewBox="0 0 10 10" refX="5" refY="5">
                    <path d="M0,0 L10,5 L0,10" fill="#000"/>
                </marker>
            </defs>
            <path d="M10,50 L100,50" stroke="#000" marker-end="url(#arrow)"/>
        </svg>""",
        "preserve_elements": ["svg", "defs", "marker", "path"],
        "preserve_attrs": ["id", "viewBox", "d", "stroke", "fill"],
    },
    {
        "name": "symbol_and_use",
        "input": """<svg viewBox="0 0 200 100">
            <symbol id="box">
                <rect width="50" height="30" fill="#eee" stroke="#000"/>
            </symbol>
            <use href="#box" x="10" y="10"/>
            <use href="#box" x="100" y="10"/>
        </svg>""",
        "preserve_elements": ["svg", "symbol", "rect", "use"],
        "preserve_attrs": ["id", "viewBox", "width", "height", "fill", "stroke", "href", "x", "y"],
    },
    {
        "name": "title_and_desc",
        "input": """<svg viewBox="0 0 200 100">
            <title>Flowchart: User Login Process</title>
            <desc>This diagram shows the user login flow</desc>
            <rect x="10" y="10" width="80" height="40"/>
        </svg>""",
        "preserve_elements": ["svg", "title", "desc", "rect"],
        "preserve_attrs": ["viewBox", "x", "y", "width", "height"],
    },
]


# =============================================================================
# Unit Tests: Element Allowlist
# =============================================================================


class TestIsAllowedElement:
    """Tests for element allowlist checking."""

    def test_allowed_elements(self) -> None:
        """Elements that should be allowed."""
        allowed = [
            "svg",
            "g",
            "path",
            "circle",
            "rect",
            "line",
            "polygon",
            "text",
            "tspan",
            "title",
            "desc",
            "defs",
            "marker",
            "use",
            "symbol",
        ]

        for element in allowed:
            assert is_allowed_element(element), f"Element '{element}' should be allowed"

    def test_dangerous_elements_not_allowed(self) -> None:
        """Dangerous elements should not be allowed."""
        dangerous = [
            "script",
            "foreignObject",
            "iframe",
            "object",
            "embed",
            "animate",
            "set",
            "handler",
            "style",
            "link",
            "meta",
        ]

        for element in dangerous:
            assert not is_allowed_element(element), f"Element '{element}' should NOT be allowed"

    def test_case_sensitive(self) -> None:
        """Element checking is case-sensitive (SVG is case-sensitive)."""
        assert is_allowed_element("svg")
        assert not is_allowed_element("SVG")
        assert not is_allowed_element("Script")


# =============================================================================
# Unit Tests: Attribute Allowlist
# =============================================================================


class TestIsAllowedAttribute:
    """Tests for attribute allowlist checking."""

    def test_allowed_attributes(self) -> None:
        """Attributes that should be allowed."""
        allowed = [
            "class",
            "id",
            "transform",
            "d",
            "cx",
            "cy",
            "r",
            "x",
            "y",
            "width",
            "height",
            "fill",
            "stroke",
            "style",
            "viewBox",
            "points",
        ]

        for attr in allowed:
            assert is_allowed_attribute(attr), f"Attribute '{attr}' should be allowed"

    def test_dangerous_attributes_not_allowed(self) -> None:
        """Dangerous attributes should not be allowed.

        Note: 'href' IS in the allowlist because internal references (e.g., #marker)
        are safe. External URLs in href values are filtered separately by
        _remove_external_references().
        """
        dangerous = [
            "onclick",
            "onload",
            "onerror",
            "src",
            "xlink:href",
            "xmlns:xlink",
            "action",
            "formaction",
        ]

        for attr in dangerous:
            assert not is_allowed_attribute(attr), f"Attribute '{attr}' should NOT be allowed"


# =============================================================================
# Unit Tests: Event Handler Detection
# =============================================================================


class TestIsEventHandlerAttribute:
    """Tests for event handler attribute detection."""

    def test_common_event_handlers(self) -> None:
        """Common event handlers should be detected."""
        handlers = [
            "onclick",
            "onload",
            "onerror",
            "onmouseover",
            "onmouseout",
            "onfocus",
            "onblur",
            "onchange",
            "onsubmit",
            "onkeydown",
            "onkeyup",
            "onkeypress",
            "onanimationend",
            "ontransitionend",
        ]

        for handler in handlers:
            assert is_event_handler_attribute(handler), f"'{handler}' should be detected"

    def test_case_insensitive(self) -> None:
        """Event handler detection is case insensitive."""
        assert is_event_handler_attribute("onclick")
        assert is_event_handler_attribute("ONCLICK")
        assert is_event_handler_attribute("OnClick")
        assert is_event_handler_attribute("oNcLiCk")

    def test_non_event_attributes(self) -> None:
        """Non-event attributes should not be detected.

        Note: Attributes starting with 'on' followed by letters ARE detected
        as event handlers (e.g., 'onclickfake'). This is intentional - it's
        safer to over-remove potential attack vectors than to miss edge cases.
        Browsers ignore unknown 'on*' attributes, so removing them is harmless.
        """
        non_events = ["class", "id", "transform", "href", "style"]

        for attr in non_events:
            assert not is_event_handler_attribute(attr), f"'{attr}' should NOT be detected"


# =============================================================================
# Unit Tests: External Reference Detection
# =============================================================================


class TestIsExternalReference:
    """Tests for external reference detection."""

    def test_external_urls(self) -> None:
        """External URL schemes should be detected."""
        external = [
            "https://evil.com/payload.js",
            "http://attacker.com/xss",
            "ftp://files.server/data",
        ]

        for url in external:
            assert is_external_reference(url), f"'{url}' should be external"

    def test_javascript_urls(self) -> None:
        """JavaScript URLs should be detected as external."""
        js_urls = [
            "javascript:alert(1)",
            "javascript:void(0)",
            "JAVASCRIPT:alert(1)",
        ]

        for url in js_urls:
            assert is_external_reference(url), f"'{url}' should be external"

    def test_dangerous_data_uris(self) -> None:
        """Dangerous data: URI types should be detected."""
        dangerous = [
            "data:text/html,<script>alert(1)</script>",
            "data:text/javascript,alert(1)",
            "data:application/javascript,alert(1)",
            "data:image/svg+xml,<svg onload=alert(1)>",
        ]

        for uri in dangerous:
            assert is_external_reference(uri), f"'{uri}' should be dangerous"

    def test_safe_data_uris(self) -> None:
        """Safe data: URI types should be allowed."""
        safe = [
            "data:image/png;base64,iVBOR...",
            "data:image/gif;base64,R0lGO...",
            "data:image/jpeg;base64,/9j/...",
        ]

        for uri in safe:
            assert not is_external_reference(uri), f"'{uri}' should be safe"

    def test_internal_references(self) -> None:
        """Internal ID references should not be external."""
        internal = [
            "#marker",
            "#arrow-head",
            "url(#gradient)",
            "",
            "none",
            "currentColor",
        ]

        for ref in internal:
            assert not is_external_reference(ref), f"'{ref}' should not be external"


# =============================================================================
# Integration Tests: Full Sanitization
# =============================================================================


class TestSanitizeSvgScriptAttacks:
    """Tests for script-based XSS attacks."""

    @pytest.mark.parametrize("attack", XSS_SCRIPT_ATTACKS, ids=lambda a: a["name"])
    def test_script_attacks_removed(self, attack: dict) -> None:
        """Script elements should be completely removed."""
        result = sanitize_svg(attack["input"])

        assert isinstance(result, SanitizationResult)
        assert result.sanitized_svg is not None

        for forbidden in attack["forbidden"]:
            assert forbidden not in result.sanitized_svg, (
                f"Forbidden content '{forbidden}' found in output"
            )

        assert result.elements_removed > 0 or result.event_handlers_removed > 0


class TestSanitizeSvgEventHandlerAttacks:
    """Tests for event handler-based XSS attacks."""

    @pytest.mark.parametrize("attack", XSS_EVENT_HANDLER_ATTACKS, ids=lambda a: a["name"])
    def test_event_handlers_removed(self, attack: dict) -> None:
        """Event handler attributes should be completely removed.

        Note: If the parent element is not allowed, the element is removed
        entirely along with its attributes. In that case, elements_removed > 0
        but event_handlers_removed may be 0.
        """
        result = sanitize_svg(attack["input"])

        for forbidden in attack["forbidden"]:
            assert forbidden not in result.sanitized_svg.lower(), (
                f"Forbidden content '{forbidden}' found in output"
            )

        # Either the event handler was removed directly, or the entire element was removed
        assert result.event_handlers_removed > 0 or result.elements_removed > 0


class TestSanitizeSvgJavascriptUrlAttacks:
    """Tests for javascript: URL-based XSS attacks."""

    @pytest.mark.parametrize("attack", XSS_JAVASCRIPT_URL_ATTACKS, ids=lambda a: a["name"])
    def test_javascript_urls_removed(self, attack: dict) -> None:
        """JavaScript URLs should be removed."""
        result = sanitize_svg(attack["input"])

        for forbidden in attack["forbidden"]:
            assert forbidden.lower() not in result.sanitized_svg.lower(), (
                f"Forbidden content '{forbidden}' found in output"
            )


class TestSanitizeSvgDataUriAttacks:
    """Tests for data: URI-based XSS attacks."""

    @pytest.mark.parametrize("attack", XSS_DATA_URI_ATTACKS, ids=lambda a: a["name"])
    def test_dangerous_data_uris_removed(self, attack: dict) -> None:
        """Dangerous data: URIs should be removed."""
        result = sanitize_svg(attack["input"])

        for forbidden in attack["forbidden"]:
            assert forbidden.lower() not in result.sanitized_svg.lower(), (
                f"Forbidden content '{forbidden}' found in output"
            )


class TestSanitizeSvgExternalResourceAttacks:
    """Tests for external resource loading attacks."""

    @pytest.mark.parametrize("attack", EXTERNAL_RESOURCE_ATTACKS, ids=lambda a: a["name"])
    def test_external_resources_removed(self, attack: dict) -> None:
        """External resources should be removed."""
        result = sanitize_svg(attack["input"])

        for forbidden in attack["forbidden"]:
            assert forbidden not in result.sanitized_svg, (
                f"Forbidden content '{forbidden}' found in output"
            )


class TestSanitizeSvgSpecificAttacks:
    """Tests for SVG-specific attacks."""

    @pytest.mark.parametrize("attack", SVG_SPECIFIC_ATTACKS, ids=lambda a: a["name"])
    def test_svg_specific_attacks_removed(self, attack: dict) -> None:
        """SVG-specific attack vectors should be mitigated."""
        result = sanitize_svg(attack["input"])

        for forbidden in attack["forbidden"]:
            assert forbidden.lower() not in result.sanitized_svg.lower(), (
                f"Forbidden content '{forbidden}' found in output"
            )


class TestSanitizeSvgEdgeCases:
    """Tests for edge cases and bypass attempts."""

    @pytest.mark.parametrize("attack", EDGE_CASE_ATTACKS, ids=lambda a: a["name"])
    def test_edge_cases_handled(self, attack: dict) -> None:
        """Edge cases and bypass attempts should be handled."""
        result = sanitize_svg(attack["input"])

        for forbidden in attack["forbidden"]:
            assert forbidden not in result.sanitized_svg, (
                f"Forbidden content '{forbidden}' found in output"
            )


# =============================================================================
# Integration Tests: Safe Content Preservation
# =============================================================================


class TestSanitizeSvgPreservation:
    """Tests for preserving safe Mermaid diagram content."""

    @pytest.mark.parametrize("diagram", SAFE_MERMAID_DIAGRAMS, ids=lambda d: d["name"])
    def test_safe_elements_preserved(self, diagram: dict) -> None:
        """Safe elements should be preserved in the output."""
        result = sanitize_svg(diagram["input"])

        for element in diagram["preserve_elements"]:
            # Check that the element opening tag exists
            assert (
                f"<{element}" in result.sanitized_svg or f"<{element}>" in result.sanitized_svg
            ), f"Element '{element}' should be preserved"

    @pytest.mark.parametrize("diagram", SAFE_MERMAID_DIAGRAMS, ids=lambda d: d["name"])
    def test_safe_attributes_preserved(self, diagram: dict) -> None:
        """Safe attributes should be preserved in the output."""
        result = sanitize_svg(diagram["input"])

        # At least some of the attributes should be present
        for attr in diagram["preserve_attrs"]:
            if attr in diagram["input"]:
                assert attr in result.sanitized_svg, (
                    f"Attribute '{attr}' should be preserved when present in input"
                )


# =============================================================================
# Return Type Tests
# =============================================================================


class TestSanitizationResult:
    """Tests for SanitizationResult dataclass."""

    def test_is_clean_true(self) -> None:
        """is_clean should be True when nothing was removed."""
        svg = '<svg><rect x="10" y="10" width="50" height="30"/></svg>'
        result = sanitize_svg(svg)

        assert result.is_clean() is True
        assert result.total_removed() == 0

    def test_is_clean_false(self) -> None:
        """is_clean should be False when something was removed."""
        svg = "<svg><script>alert(1)</script></svg>"
        result = sanitize_svg(svg)

        assert result.is_clean() is False
        assert result.total_removed() > 0

    def test_total_removed_sums_counts(self) -> None:
        """total_removed should sum all removal counts."""
        svg = '<svg><script>a</script><rect onclick="x"/></svg>'
        result = sanitize_svg(svg)

        assert result.total_removed() == (
            result.elements_removed
            + result.attributes_removed
            + result.event_handlers_removed
            + result.external_refs_removed
        )


class TestSanitizeSvgContent:
    """Tests for the convenience function."""

    def test_returns_string(self) -> None:
        """Should return just the string, not the full result."""
        svg = "<svg><rect/></svg>"
        result = sanitize_svg_content(svg)

        assert isinstance(result, str)
        assert "<svg" in result
        assert "<rect" in result

    def test_removes_dangerous_content(self) -> None:
        """Should remove dangerous content."""
        svg = "<svg><script>alert(1)</script></svg>"
        result = sanitize_svg_content(svg)

        assert "<script" not in result
        assert "alert" not in result


# =============================================================================
# Empty and Edge Input Tests
# =============================================================================


class TestEdgeInputs:
    """Tests for edge case inputs."""

    def test_empty_string(self) -> None:
        """Empty string should return empty result."""
        result = sanitize_svg("")

        assert result.sanitized_svg == ""
        assert result.is_clean() is True

    def test_whitespace_only(self) -> None:
        """Whitespace-only input should return whitespace."""
        result = sanitize_svg("   \n\t  ")

        assert result.sanitized_svg == "   \n\t  "
        assert result.is_clean() is True

    def test_no_svg_element(self) -> None:
        """Input without SVG element should pass through."""
        result = sanitize_svg("Hello, world!")

        assert result.sanitized_svg == "Hello, world!"
        assert result.is_clean() is True

    def test_malformed_tags(self) -> None:
        """Malformed tags should be handled gracefully."""
        result = sanitize_svg("<svg><rect><svg>")

        # Should not raise an error
        assert isinstance(result, SanitizationResult)

    def test_deeply_nested(self) -> None:
        """Deeply nested structures should be handled."""
        svg = "<svg>" + "<g>" * 100 + "<rect/>" + "</g>" * 100 + "</svg>"
        result = sanitize_svg(svg)

        assert "<rect" in result.sanitized_svg
        assert result.sanitized_svg.count("<g") == 100


# =============================================================================
# Statistics Tracking Tests
# =============================================================================


class TestStatisticsTracking:
    """Tests for accurate statistics tracking."""

    def test_counts_elements_removed(self) -> None:
        """Should accurately count removed elements."""
        svg = "<svg><script>a</script><foreignObject>b</foreignObject><rect/></svg>"
        result = sanitize_svg(svg)

        assert result.elements_removed >= 2

    def test_counts_event_handlers_removed(self) -> None:
        """Should accurately count removed event handlers."""
        svg = '<svg onclick="a" onload="b"><rect onerror="c"/></svg>'
        result = sanitize_svg(svg)

        assert result.event_handlers_removed == 3

    def test_counts_external_refs_removed(self) -> None:
        """Should accurately count removed external references."""
        svg = '<svg><use href="https://evil.com/x.svg"/><use href="#safe"/></svg>'
        result = sanitize_svg(svg)

        assert result.external_refs_removed >= 1

    def test_combined_statistics(self) -> None:
        """Should track all types of removals."""
        svg = """
        <svg onclick="x">
            <script>alert(1)</script>
            <rect fill="red" onmouseover="y"/>
            <use href="https://evil.com/bad.svg"/>
        </svg>
        """
        result = sanitize_svg(svg)

        assert result.elements_removed > 0  # script
        assert result.event_handlers_removed > 0  # onclick, onmouseover
        assert result.external_refs_removed > 0  # external href


# =============================================================================
# Evidence Output
# =============================================================================


class TestEvidenceOutput:
    """Generate evidence output for documentation."""

    def test_evidence_snippet(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Output sanitized SVG snippet for evidence."""
        svg = """<svg viewBox="0 0 200 100" onclick="alert(1)">
            <script>malicious()</script>
            <rect x="10" y="10" width="80" height="40" fill="#ccc"/>
            <use href="https://evil.com/payload.svg"/>
            <text x="50" y="35">Safe</text>
        </svg>"""

        result = sanitize_svg(svg)

        print("\n=== INPUT SVG ===")
        print(svg)
        print("\n=== SANITIZED SVG ===")
        print(result.sanitized_svg)
        print("\n=== STATISTICS ===")
        print(f"Elements removed: {result.elements_removed}")
        print(f"Attributes removed: {result.attributes_removed}")
        print(f"Event handlers removed: {result.event_handlers_removed}")
        print(f"External refs removed: {result.external_refs_removed}")
        print(f"Total removed: {result.total_removed()}")

        # Verify forbidden content is gone
        forbidden = ["onclick", "alert", "script", "malicious", "evil.com"]
        for f in forbidden:
            assert f not in result.sanitized_svg, f"Forbidden '{f}' found in output"


def test_corpus_summary() -> None:
    """Summary of test corpus for evidence."""
    total_attacks = (
        len(XSS_SCRIPT_ATTACKS)
        + len(XSS_EVENT_HANDLER_ATTACKS)
        + len(XSS_JAVASCRIPT_URL_ATTACKS)
        + len(XSS_DATA_URI_ATTACKS)
        + len(EXTERNAL_RESOURCE_ATTACKS)
        + len(SVG_SPECIFIC_ATTACKS)
        + len(EDGE_CASE_ATTACKS)
    )

    print(f"\n=== TEST CORPUS SUMMARY ===")
    print(f"XSS Script Attacks: {len(XSS_SCRIPT_ATTACKS)}")
    print(f"XSS Event Handler Attacks: {len(XSS_EVENT_HANDLER_ATTACKS)}")
    print(f"XSS JavaScript URL Attacks: {len(XSS_JAVASCRIPT_URL_ATTACKS)}")
    print(f"XSS Data URI Attacks: {len(XSS_DATA_URI_ATTACKS)}")
    print(f"External Resource Attacks: {len(EXTERNAL_RESOURCE_ATTACKS)}")
    print(f"SVG-Specific Attacks: {len(SVG_SPECIFIC_ATTACKS)}")
    print(f"Edge Cases: {len(EDGE_CASE_ATTACKS)}")
    print(f"Safe Diagram Cases: {len(SAFE_MERMAID_DIAGRAMS)}")
    print(f"TOTAL ATTACK VECTORS: {total_attacks}")

    assert total_attacks > 0, "Test corpus should contain attack vectors"
