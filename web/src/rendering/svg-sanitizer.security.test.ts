/**
 * SVG Sanitization Security Tests
 *
 * Regression corpus for SVG sanitization to prevent XSS attacks from Mermaid diagrams.
 *
 * @module rendering/svg-sanitizer.security.test
 */

import { describe, it, expect } from 'vitest'
import {
  sanitizeSvg,
  sanitizeSvgRegex,
  hasDangerousSvgContent,
} from './svg-sanitizer'

// =============================================================================
// Test Corpus - Attack Vectors
// =============================================================================

/**
 * XSS attack vectors via SVG.
 * Each test case includes:
 * - name: Description of the attack
 * - input: Malicious SVG
 * - forbidden: Patterns that must NOT appear in output
 * - preserved: Patterns that SHOULD appear in output (if any)
 */
const XSS_ATTACK_VECTORS = [
  {
    name: 'script tag injection',
    input: '<svg><script>alert("XSS")</script></svg>',
    forbidden: ['<script', 'alert', '</script>'],
    preserved: ['<svg'],
  },
  {
    name: 'onload event handler',
    input: '<svg onload="alert(\'XSS\')"><circle cx="50" cy="50" r="40"/></svg>',
    forbidden: ['onload', 'alert'],
    preserved: ['<circle', 'cx="50"'],
  },
  {
    name: 'onclick event handler',
    input: '<svg><rect onclick="alert(\'XSS\')" width="100" height="100"/></svg>',
    forbidden: ['onclick', 'alert'],
    preserved: ['<rect', 'width="100"'],
  },
  {
    name: 'onerror event handler',
    input: '<svg><image onerror="alert(\'XSS\')" href="x"/></svg>',
    forbidden: ['onerror', 'alert'],
    preserved: ['<svg'],
  },
  {
    name: 'javascript: URL in href - a element forbidden',
    input: '<svg><a href="javascript:alert(\'XSS\')"><text>Click</text></a></svg>',
    // NOTE: <a> element is FORBIDDEN per ADR-002 §2
    forbidden: ['javascript:', 'alert', '<a'],
    preserved: ['<svg'], // text content is removed along with the <a> element
  },
  {
    name: 'data: URL with HTML - a element forbidden',
    input: '<svg><a href="data:text/html,&lt;script&gt;alert(\'XSS\')&lt;/script&gt;"><text>Click</text></a></svg>',
    forbidden: ['data:text/html', '<script', '<a'],
    preserved: ['<svg'],
  },
  {
    name: 'foreignObject with HTML',
    input: '<svg><foreignObject><body onload="alert(\'XSS\')"></body></foreignObject></svg>',
    forbidden: ['<foreignObject', 'onload', 'alert'],
    preserved: ['<svg'],
  },
  {
    name: 'iframe injection',
    input: '<svg><foreignObject><iframe src="https://evil.com"></iframe></foreignObject></svg>',
    forbidden: ['<iframe', 'evil.com'],
    preserved: ['<svg'],
  },
  {
    name: 'style tag with expression - FORBIDDEN per ADR-002 §4',
    input: '<svg><style>body{background:expression(alert("XSS"))}</style></svg>',
    // NOTE: <style> elements are FORBIDDEN per ADR-002 §4
    // They increase attack surface via CSS url(), @import, and browser quirks
    forbidden: ['<style', 'expression', 'alert'],
    preserved: ['<svg'],
  },
  {
    name: 'style element stripped entirely',
    input: '<svg><style>.foo{fill:red}</style><circle cx="50" cy="50" r="40"/></svg>',
    forbidden: ['<style', 'fill:red'],
    preserved: ['<svg', '<circle'],
  },
  {
    name: 'animate element - FORBIDDEN per ADR-002 §2 (SMIL)',
    input: '<svg><animate onbegin="alert(\'XSS\')" attributeName="x"/></svg>',
    // NOTE: SMIL animation elements are FORBIDDEN per ADR-002 §2
    forbidden: ['<animate', 'onbegin', 'alert'],
    preserved: ['<svg'],
  },
  {
    name: 'set element - FORBIDDEN per ADR-002 §2 (SMIL)',
    input: '<svg><set onbegin="alert(\'XSS\')" attributeName="x"/></svg>',
    // NOTE: SMIL animation elements are FORBIDDEN per ADR-002 §2
    forbidden: ['<set', 'onbegin', 'alert'],
    preserved: ['<svg'],
  },
  {
    name: 'use element with external reference',
    input: '<svg><use href="https://evil.com/malicious.svg#payload"/></svg>',
    forbidden: ['https://evil.com', 'malicious.svg'],
    preserved: ['<svg'],
  },
  {
    name: 'xlink:href javascript - a element forbidden',
    input: '<svg xmlns:xlink="http://www.w3.org/1999/xlink"><a xlink:href="javascript:alert(\'XSS\')"><text>Click</text></a></svg>',
    forbidden: ['javascript:', 'alert', '<a'],
    preserved: ['<svg'],
  },
  {
    name: 'nested script in foreignObject',
    input: '<svg><foreignObject><div><script>alert(1)</script></div></foreignObject></svg>',
    forbidden: ['<foreignObject', '<script', 'alert'],
    preserved: ['<svg'],
  },
  {
    name: 'SVG with multiple event handlers',
    input: '<svg onload="alert(1)" onclick="alert(2)" onmouseover="alert(3)"><circle cx="50" cy="50" r="40"/></svg>',
    forbidden: ['onload', 'onclick', 'onmouseover', 'alert'],
    preserved: ['<circle', 'cx="50"'],
  },
]

// =============================================================================
// Safe SVG Test Cases
// =============================================================================

const SAFE_SVG_CASES = [
  {
    name: 'simple flowchart',
    input: '<svg viewBox="0 0 100 100"><g><rect x="10" y="10" width="80" height="40" fill="white"/></g></svg>',
    preserved: ['<rect', 'fill="white"'],
  },
  {
    name: 'text element',
    input: '<svg><text x="50" y="50" text-anchor="middle">Hello</text></svg>',
    preserved: ['<text', 'text-anchor', 'Hello'],
  },
  {
    name: 'path element',
    input: '<svg><path d="M10 10 L90 90" stroke="black"/></svg>',
    preserved: ['<path', 'stroke="black"'],
  },
  {
    name: 'marker element',
    input: '<svg><defs><marker id="arrow" viewBox="0 0 10 10"><path d="M0 0 L10 5 L0 10"/></marker></defs></svg>',
    preserved: ['<marker', '<path'],
  },
  {
    name: 'named colors in fill',
    input: '<svg><circle cx="50" cy="50" r="30" fill="red" stroke="blue"/></svg>',
    preserved: ['fill="red"', 'stroke="blue"', '<circle'],
  },
  {
    name: 'linearGradient element - ALLOWED per ADR-002 §1',
    input: '<svg><defs><linearGradient id="grad1"><stop offset="0%" stop-color="red"/><stop offset="100%" stop-color="blue"/></linearGradient></defs></svg>',
    preserved: ['<linearGradient', '<stop'],
  },
  {
    name: 'radialGradient element - ALLOWED per ADR-002 §1',
    input: '<svg><defs><radialGradient id="grad2"><stop offset="0%" stop-color="green"/></radialGradient></defs></svg>',
    preserved: ['<radialGradient', '<stop'],
  },
]

// =============================================================================
// Tests
// =============================================================================

describe('SVG Sanitization', () => {
  describe('XSS Attack Prevention', () => {
    for (const { name, input, forbidden, preserved } of XSS_ATTACK_VECTORS) {
      it(`blocks ${name}`, () => {
        const result = sanitizeSvg(input)

        // Check that forbidden patterns are absent
        for (const pattern of forbidden) {
          expect(result.toLowerCase()).not.toContain(pattern.toLowerCase())
        }

        // Check that safe content is preserved
        for (const pattern of preserved) {
          expect(result).toContain(pattern)
        }
      })
    }
  })

  describe('Safe SVG Preservation', () => {
    for (const { name, input, preserved } of SAFE_SVG_CASES) {
      it(`preserves ${name}`, () => {
        const result = sanitizeSvg(input)

        // Check that safe patterns are preserved
        for (const pattern of preserved) {
          expect(result).toContain(pattern)
        }
      })
    }
  })

  describe('ADR-002 strict allowlist enforcement', () => {
    const forbiddenElements = [
      'use',
      'symbol',
      'title',
      'desc',
      'textPath',
      'script',
      'foreignObject',
    ]

    for (const elementName of forbiddenElements) {
      it(`removes forbidden <${elementName}> elements`, () => {
        const result = sanitizeSvg(
          `<svg><g><${elementName} id="bad" href="#x" onclick="alert(1)">bad</${elementName}></g><circle cx="1" cy="2" r="3"/></svg>`
        )

        expect(result.toLowerCase()).not.toContain(`<${elementName.toLowerCase()}`)
        expect(result).not.toContain('bad')
        expect(result).toContain('<circle')
      })
    }

    it('removes broad non-ADR attributes while preserving allowed attributes', () => {
      const result = sanitizeSvg(
        '<svg viewBox="0 0 10 10" preserveAspectRatio="xMidYMid" transform="scale(2)" tabindex="0" aria-labelledby="t" data-test="x"><rect x="1" y="2" width="3" height="4" markerWidth="5" refX="6"/></svg>'
      )

      expect(result).toContain('viewBox="0 0 10 10"')
      expect(result).toContain('width="3"')
      expect(result).not.toContain('preserveAspectRatio')
      expect(result).not.toContain('transform=')
      expect(result).not.toContain('tabindex')
      expect(result).not.toContain('aria-labelledby')
      expect(result).not.toContain('data-test')
      expect(result).not.toContain('markerWidth')
      expect(result).not.toContain('refX')
    })

    it('removes gradient attributes not listed in ADR-002 v0.1', () => {
      const result = sanitizeSvg(
        '<svg><defs><linearGradient id="g" gradientUnits="userSpaceOnUse"><stop offset="0%" stop-color="red" stop-opacity="0.5"/></linearGradient></defs></svg>'
      )

      expect(result).toContain('<linearGradient')
      expect(result).toContain('<stop')
      expect(result).not.toContain('gradientUnits')
      expect(result).not.toContain('offset=')
      expect(result).not.toContain('stop-color')
      expect(result).not.toContain('stop-opacity')
    })
  })

  describe('ADR-002 URL/reference policy', () => {
    const unsafeValues = [
      'javascript:alert(1)',
      'data:image/svg+xml,<svg/>',
      'file:///tmp/x.svg#id',
      'https://example.test/x.svg#id',
      'http://example.test/x.svg#id',
      'mailto:test@example.test',
      'relative.svg#id',
      '/relative.svg#id',
      'url(javascript:alert(1))',
      'url(data:image/svg+xml,<svg/>)',
      'url(file:///tmp/x.svg#id)',
      'url(https://example.test/x.svg#id)',
      'url(http://example.test/x.svg#id)',
      'url(mailto:test@example.test)',
      'url(relative.svg#id)',
    ]

    it('allows only internal fragment url(...) for marker references', () => {
      const safe = sanitizeSvg(
        '<svg><path marker-start="url(#arrow)" marker-mid="url(\'#mid\')" marker-end="url(&quot;#end&quot;)" d="M0 0 L1 1"/></svg>'
      )

      expect(safe).toContain('marker-start="url(#arrow)"')
      expect(safe).toContain("marker-mid=\"url('#mid')\"")
      expect(safe).toContain('marker-end="url(&quot;#end&quot;)"')

      for (const value of unsafeValues) {
        const result = sanitizeSvg(`<svg><path marker-start="${value}" d="M0 0 L1 1"/></svg>`)
        expect(result).not.toContain('marker-start')
      }
    })

    it('allows colors and internal fragment url(...) for fill/stroke only', () => {
      const safe = sanitizeSvg(
        '<svg><rect fill="red" stroke="url(#line)" width="5" height="5"/></svg>'
      )

      expect(safe).toContain('fill="red"')
      expect(safe).toContain('stroke="url(#line)"')

      for (const value of unsafeValues) {
        const result = sanitizeSvg(`<svg><rect fill="${value}" stroke="${value}" width="5" height="5"/></svg>`)
        expect(result).not.toContain('fill=')
        expect(result).not.toContain('stroke=')
      }
    })

    it('removes href/xlink/style attributes entirely because ADR-002 v0.1 does not enable them', () => {
      const result = sanitizeSvg(
        '<svg xmlns:xlink="http://www.w3.org/1999/xlink"><path href="#ok" xlink:href="#ok" style="fill:url(#ok)" d="M0 0 L1 1"/></svg>'
      )

      expect(result).not.toContain('href=')
      expect(result).not.toContain('xlink:href')
      expect(result).not.toContain('style=')
      expect(result).toContain('d="M0 0 L1 1"')
    })
  })

  describe('Dangerous Content Detection', () => {
    it('detects script tags', () => {
      expect(hasDangerousSvgContent('<svg><script>alert(1)</script></svg>')).toBe(true)
    })

    it('detects event handlers', () => {
      expect(hasDangerousSvgContent('<svg onload="alert(1)"></svg>')).toBe(true)
    })

    it('detects javascript: URLs', () => {
      expect(hasDangerousSvgContent('<svg><a href="javascript:alert(1)"></a></svg>')).toBe(true)
    })

    it('detects foreignObject', () => {
      expect(hasDangerousSvgContent('<svg><foreignObject></foreignObject></svg>')).toBe(true)
    })

    it('detects a element (FORBIDDEN per ADR-002 §2)', () => {
      expect(hasDangerousSvgContent('<svg><a href="https://evil.com"></a></svg>')).toBe(true)
    })

    it('detects style element (FORBIDDEN per ADR-002 §4)', () => {
      expect(hasDangerousSvgContent('<svg><style>.foo{fill:red}</style></svg>')).toBe(true)
    })

    it('detects animate element (SMIL, FORBIDDEN per ADR-002 §2)', () => {
      expect(hasDangerousSvgContent('<svg><animate attributeName="x"/></svg>')).toBe(true)
    })

    it('detects set element (SMIL, FORBIDDEN per ADR-002 §2)', () => {
      expect(hasDangerousSvgContent('<svg><set attributeName="x"/></svg>')).toBe(true)
    })

    it('detects image element (FORBIDDEN per ADR-002 §2)', () => {
      expect(hasDangerousSvgContent('<svg><image href="https://evil.com/img.png"/></svg>')).toBe(true)
    })

    it('returns false for safe SVG', () => {
      expect(hasDangerousSvgContent('<svg><circle cx="50" cy="50" r="40"/></svg>')).toBe(false)
    })
  })

  describe('Regex Fallback', () => {
    it('removes script tags', () => {
      const result = sanitizeSvgRegex('<svg><script>alert(1)</script></svg>')
      expect(result).not.toContain('<script')
      expect(result).not.toContain('alert')
    })

    it('removes event handlers', () => {
      const result = sanitizeSvgRegex('<svg onload="alert(1)"></svg>')
      expect(result).not.toContain('onload')
      expect(result).not.toContain('alert')
    })

    it('removes style tags (FORBIDDEN per ADR-002 §4)', () => {
      const result = sanitizeSvgRegex('<svg><style>.foo{fill:red}</style><circle/></svg>')
      expect(result).not.toContain('<style')
      expect(result).not.toContain('fill:red')
      expect(result).toContain('<circle')
    })

    it('removes a tags (FORBIDDEN per ADR-002 §2)', () => {
      const result = sanitizeSvgRegex('<svg><a href="https://evil.com"><text>Click</text></a></svg>')
      expect(result).not.toContain('<a')
      expect(result).not.toContain('evil.com')
    })

    it('removes animate elements (SMIL, FORBIDDEN per ADR-002 §2)', () => {
      const result = sanitizeSvgRegex('<svg><animate attributeName="x"/></svg>')
      expect(result).not.toContain('<animate')
    })

    it('removes set elements (SMIL, FORBIDDEN per ADR-002 §2)', () => {
      const result = sanitizeSvgRegex('<svg><set attributeName="x"/></svg>')
      expect(result).not.toContain('<set')
    })

    it('preserves safe content', () => {
      const result = sanitizeSvgRegex('<svg><circle cx="50"/></svg>')
      expect(result).toContain('<circle')
      expect(result).toContain('cx="50"')
    })
  })

  describe('Edge Cases', () => {
    it('handles empty input', () => {
      expect(sanitizeSvg('')).toBe('')
    })

    it('handles non-SVG input', () => {
      expect(sanitizeSvg('<div>hello</div>')).toBe('')
    })

    it('handles malformed SVG', () => {
      const result = sanitizeSvg('<svg><circle></svg>')
      // Should not throw, may return partial content
      expect(typeof result).toBe('string')
    })

    it('preserves circle geometry', () => {
      const result = sanitizeSvg('<svg><circle cx="50" cy="50" r="40" fill="red"/></svg>')
      expect(result.toLowerCase()).toContain('circle')
      expect(result).toContain('cx="50"')
      expect(result).toContain('fill="red"')
    })

    it('preserves rect geometry', () => {
      const result = sanitizeSvg('<svg><rect x="10" y="10" width="80" height="40"/></svg>')
      expect(result.toLowerCase()).toContain('rect')
      expect(result).toContain('x="10"')
      expect(result).toContain('width="80"')
    })
  })
})
