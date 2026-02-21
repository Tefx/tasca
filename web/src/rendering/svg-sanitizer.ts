/**
 * SVG Sanitization for Mermaid diagrams.
 *
 * SECURITY: This module sanitizes SVG output from Mermaid to prevent XSS attacks.
 *
 * ## Security Measures
 *
 * - **Element allowlist**: Only safe SVG elements are allowed
 * - **Attribute allowlist**: Only safe attributes are allowed
 * - **Event handler removal**: All on* attributes are removed
 * - **External reference removal**: xlink:href to external URLs are blocked
 *
 * ## References
 *
 * - ADR-002: SVG Sanitization Strategy
 * - https://github.com/mermaid-js/mermaid/security/advisories/GHSA-7rqq-prvp-x9jh
 */

// =============================================================================
// Allowlists
// =============================================================================

/**
 * Allowed SVG elements.
 *
 * These are the elements commonly used in Mermaid diagrams.
 * Elements like script, foreignObject, iframe are NOT allowed.
 */
const ALLOWED_SVG_ELEMENTS = new Set([
  // Root
  'svg',
  // Structure
  'g',
  'defs',
  'symbol',
  'marker',
  'use',
  'title',
  'desc',
  // Shapes
  'path',
  'circle',
  'rect',
  'line',
  'polygon',
  'polyline',
  'ellipse',
  // Text
  'text',
  'tspan',
  'textPath',
  // Styling
  'style',
  // Links (will have href sanitized)
  'a',
])

/**
 * Allowed SVG attributes.
 *
 * These are safe attributes that cannot execute JavaScript.
 * All on* event handlers are explicitly disallowed.
 */
const ALLOWED_SVG_ATTRIBUTES = new Set([
  // Core
  'id',
  'class',
  'style',
  // Presentation
  'fill',
  'stroke',
  'stroke-width',
  'stroke-linecap',
  'stroke-linejoin',
  'stroke-dasharray',
  'stroke-dashoffset',
  'stroke-miterlimit',
  'stroke-opacity',
  'fill-opacity',
  'opacity',
  'color',
  // Transform
  'transform',
  'transform-origin',
  // Geometry
  'x',
  'y',
  'x1',
  'y1',
  'x2',
  'y2',
  'cx',
  'cy',
  'r',
  'rx',
  'ry',
  'width',
  'height',
  'd',
  'points',
  'viewBox',
  'preserveAspectRatio',
  // Text
  'font-family',
  'font-size',
  'font-weight',
  'font-style',
  'text-anchor',
  'dominant-baseline',
  'letter-spacing',
  'word-spacing',
  // Links
  'href',
  'xlink:href',
  'target',
  'rel',
  // Marker
  'marker-start',
  'marker-mid',
  'marker-end',
  'markerWidth',
  'markerHeight',
  'markerUnits',
  'refX',
  'refY',
  'orient',
  // Use
  'xlink:title',
  // Accessibility
  'role',
  'aria-label',
  'aria-labelledby',
  'aria-describedby',
  'aria-hidden',
  'tabindex',
])

/**
 * Regex to match event handler attributes (onclick, onload, onerror, etc.)
 */
const EVENT_HANDLER_PATTERN = /^on/i

/**
 * Regex to match external URLs (http, https, ftp, etc.)
 */
const EXTERNAL_URL_PATTERN = /^(https?:|ftp:|data:|javascript:|vbscript:)/i

/**
 * Safe URL schemes for href attributes.
 */
const SAFE_URL_SCHEMES = ['#', 'mailto:']

// =============================================================================
// Sanitization Functions
// =============================================================================

/**
 * Sanitize an SVG string by removing dangerous elements and attributes.
 *
 * This function:
 * 1. Parses the SVG into a DOM fragment
 * 2. Removes disallowed elements
 * 3. Removes disallowed attributes including event handlers
 * 4. Sanitizes href attributes to block external URLs
 * 5. Returns the sanitized SVG string
 *
 * @param svgString - Raw SVG string (potentially untrusted)
 * @returns Sanitized SVG string safe for rendering
 *
 * @example
 * ```typescript
 * const malicious = '<svg onload="alert(1)"><script>alert(1)</script></svg>'
 * const safe = sanitizeSvg(malicious)
 * // Returns: '<svg></svg>' (script and onload removed)
 * ```
 */
export function sanitizeSvg(svgString: string): string {
  // Create a temporary container
  const template = document.createElement('template')
  template.innerHTML = svgString

  const svg = template.content.querySelector('svg')
  if (!svg) {
    // If there's no SVG root, return empty string for safety
    return ''
  }

  // Create a document fragment to work with
  const fragment = document.createDocumentFragment()

  // Clone the SVG to avoid modifying the original
  const clonedSvg = svg.cloneNode(true) as SVGSVGElement

  // Recursively sanitize the SVG tree
  sanitizeElement(clonedSvg)

  fragment.appendChild(clonedSvg)

  // Serialize back to string
  const serializer = new XMLSerializer()
  return serializer.serializeToString(fragment)
}

/**
 * Recursively sanitize an element and its children.
 *
 * @param element - Element to sanitize
 */
function sanitizeElement(element: Element): void {
  // Check if this element is allowed
  const tagName = element.tagName.toLowerCase()

  // Remove disallowed elements but keep their children (for some cases)
  if (!ALLOWED_SVG_ELEMENTS.has(tagName)) {
    // For dangerous elements, remove them entirely
    if (tagName === 'script' || tagName === 'foreignobject' || tagName === 'iframe') {
      element.remove()
      return
    }
    // For other disallowed elements, replace with their content (unwrap)
    const parent = element.parentNode
    while (element.firstChild) {
      parent?.insertBefore(element.firstChild, element)
    }
    element.remove()
    return
  }

  // Sanitize attributes
  const attributesToRemove: string[] = []
  for (const attr of Array.from(element.attributes)) {
    const attrName = attr.name.toLowerCase()

    // Remove event handlers
    if (EVENT_HANDLER_PATTERN.test(attrName)) {
      attributesToRemove.push(attr.name)
      continue
    }

    // Check if attribute is allowed
    if (!ALLOWED_SVG_ATTRIBUTES.has(attrName)) {
      attributesToRemove.push(attr.name)
      continue
    }

    // Sanitize href attributes
    if (attrName === 'href' || attrName === 'xlink:href') {
      const value = attr.value.trim()
      // Block external URLs
      if (EXTERNAL_URL_PATTERN.test(value) && !SAFE_URL_SCHEMES.some(s => value.startsWith(s))) {
        attributesToRemove.push(attr.name)
        continue
      }
    }
  }

  // Remove disallowed attributes
  for (const attrName of attributesToRemove) {
    element.removeAttribute(attrName)
  }

  // Recursively sanitize children
  for (const child of Array.from(element.children)) {
    sanitizeElement(child)
  }
}

/**
 * Check if an SVG string contains any dangerous content.
 *
 * Useful for logging and debugging.
 *
 * @param svgString - SVG string to check
 * @returns True if dangerous content is found
 */
export function hasDangerousSvgContent(svgString: string): boolean {
  const dangerousPatterns = [
    /<script\b/i,
    /<foreignobject\b/i,
    /<iframe\b/i,
    /<embed\b/i,
    /<object\b/i,
    /\bon\w+\s*=/i, // Event handlers
    /javascript:/i,
    /data:text\/html/i,
    /vbscript:/i,
  ]

  return dangerousPatterns.some(pattern => pattern.test(svgString))
}

/**
 * Remove dangerous content from SVG string using regex.
 *
 * This is a fallback for environments without DOM parsing.
 * For browser environments, prefer sanitizeSvg() instead.
 *
 * @param svgString - Raw SVG string
 * @returns Sanitized SVG string
 */
export function sanitizeSvgRegex(svgString: string): string {
  let result = svgString

  // Remove script tags and content
  result = result.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '')

  // Remove foreignObject tags and content
  result = result.replace(/<foreignobject\b[^<]*(?:(?!<\/foreignobject>)<[^<]*)*<\/foreignobject>/gi, '')

  // Remove iframe tags
  result = result.replace(/<iframe\b[^<]*(?:(?!<\/iframe>)<[^<]*)*<\/iframe>/gi, '')

  // Remove event handlers
  result = result.replace(/\s+on\w+\s*=\s*["'][^"']*["']/gi, '')

  // Remove javascript: URLs
  result = result.replace(/javascript:/gi, '')

  // Remove data:text/html URLs
  result = result.replace(/data:text\/html/gi, '')

  return result
}

export default sanitizeSvg