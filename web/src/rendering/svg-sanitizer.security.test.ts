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
    input: '<svg><a href="data:text/html,<script>alert(\'XSS\')</script>"><text>Click</text></a></svg>',
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
    input: '<svg><a xlink:href="javascript:alert(\'XSS\')"><text>Click</text></a></svg>',
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
    preserved: ['<linearGradient', '<stop', 'stop-color'],
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