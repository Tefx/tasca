/**
 * SVG Sanitization for Mermaid diagrams.
 *
 * Canonical policy source: docs/adr-002-mermaid-svg-sanitization.md.
 * This module implements ADR-002 v0.1 as a strict allowlist: any SVG
 * element, attribute, or URL/reference value not explicitly allowed there is
 * removed before the renderer imports the SVG into the document.
 */

const ALLOWED_SVG_ELEMENTS = new Set([
  'svg',
  'g',
  'defs',
  'path',
  'rect',
  'circle',
  'ellipse',
  'line',
  'polyline',
  'polygon',
  'text',
  'tspan',
  'marker',
  // DOMParser preserves camelCase names for SVG/XML; sanitizeElement lowercases.
  'lineargradient',
  'radialgradient',
  'stop',
])

const ALLOWED_SVG_ATTRIBUTES = new Set([
  'viewbox',
  'width',
  'height',
  'x',
  'y',
  'cx',
  'cy',
  'r',
  'rx',
  'ry',
  'x1',
  'y1',
  'x2',
  'y2',
  'd',
  'points',
  'fill',
  'fill-opacity',
  'stroke',
  'stroke-opacity',
  'stroke-width',
  'stroke-linecap',
  'stroke-linejoin',
  'stroke-dasharray',
  'opacity',
  'font-family',
  'font-size',
  'font-weight',
  'text-anchor',
  'dominant-baseline',
  'id',
  'class',
  'role',
  'aria-label',
  'marker-start',
  'marker-mid',
  'marker-end',
])

const MARKER_REFERENCE_ATTRIBUTES = new Set(['marker-start', 'marker-mid', 'marker-end'])
const PAINT_ATTRIBUTES = new Set(['fill', 'stroke'])

const EVENT_HANDLER_PATTERN = /^on/i
const INTERNAL_FRAGMENT_PATTERN = /^#[A-Za-z_][\w:.-]*$/
const INTERNAL_URL_FUNCTION_PATTERN = /^url\(\s*['"]?(#[A-Za-z_][\w:.-]*)['"]?\s*\)$/i
const URL_FUNCTION_PATTERN = /url\(/i
const FORBIDDEN_URL_PATTERN = /(?:javascript|data|file|https?|mailto):|\b(?:src|href)\s*=|@import/i
const RELATIVE_URL_LIKE_PATTERN = /^(?:\.?\.?\/|\/|[^\s]+\.(?:svg|png|jpg|jpeg|gif|webp)(?:[#?/].*)?)/i

/**
 * Sanitize an SVG string by removing elements/attributes outside ADR-002.
 *
 * Parsing uses DOMParser with the SVG/XML MIME type and imports nodes instead
 * of assigning untrusted markup through `innerHTML`.
 */
export function sanitizeSvg(svgString: string): string {
  const parsed = new DOMParser().parseFromString(svgString, 'image/svg+xml')
  if (parsed.querySelector('parsererror')) {
    return ''
  }

  const svg = parsed.documentElement
  if (svg.tagName.toLowerCase() !== 'svg') {
    return ''
  }

  const clonedSvg = document.importNode(svg, true) as Element
  sanitizeElement(clonedSvg)

  return new XMLSerializer().serializeToString(clonedSvg)
}

function sanitizeElement(element: Element): void {
  const tagName = element.tagName.toLowerCase()

  if (!ALLOWED_SVG_ELEMENTS.has(tagName)) {
    element.remove()
    return
  }

  sanitizeAttributes(element)

  for (const child of Array.from(element.children)) {
    sanitizeElement(child)
  }
}

function sanitizeAttributes(element: Element): void {
  const attributesToRemove: string[] = []

  for (const attr of Array.from(element.attributes)) {
    const attrName = attr.name.toLowerCase()

    if (EVENT_HANDLER_PATTERN.test(attrName) || !ALLOWED_SVG_ATTRIBUTES.has(attrName)) {
      attributesToRemove.push(attr.name)
      continue
    }

    if (!isAllowedAttributeValue(attrName, attr.value)) {
      attributesToRemove.push(attr.name)
    }
  }

  for (const attrName of attributesToRemove) {
    element.removeAttribute(attrName)
  }
}

function isAllowedAttributeValue(attrName: string, rawValue: string): boolean {
  const value = rawValue.trim()

  if (MARKER_REFERENCE_ATTRIBUTES.has(attrName)) {
    return INTERNAL_URL_FUNCTION_PATTERN.test(value)
  }

  if (PAINT_ATTRIBUTES.has(attrName)) {
    if (URL_FUNCTION_PATTERN.test(value)) {
      return INTERNAL_URL_FUNCTION_PATTERN.test(value)
    }

    return !FORBIDDEN_URL_PATTERN.test(value) && !RELATIVE_URL_LIKE_PATTERN.test(value)
  }

  return !URL_FUNCTION_PATTERN.test(value) && !FORBIDDEN_URL_PATTERN.test(value)
}

export function isSafeInternalFragmentReference(value: string): boolean {
  return INTERNAL_FRAGMENT_PATTERN.test(value.trim())
}

export function hasDangerousSvgContent(svgString: string): boolean {
  const dangerousPatterns = [
    /<(?:script|foreignobject|iframe|embed|object|audio|video|image|a|style|animate|set|animatetransform|animatemotion|use|symbol|title|desc|textpath)\b/i,
    /\bon\w+\s*=/i,
    /\b(?:href|xlink:href|style|target|rel|aria-labelledby|aria-describedby|tabindex|transform|preserveAspectRatio|markerWidth|markerHeight|markerUnits|refX|refY|orient|gradientUnits|gradientTransform|spreadMethod|fx|fy|offset|stop-color|stop-opacity)\s*=/i,
    /(?:javascript|data|file|https?|mailto):/i,
    /url\(\s*['"]?(?!#[A-Za-z_][\w:.-]*['"]?\s*\))/i,
  ]

  return dangerousPatterns.some(pattern => pattern.test(svgString))
}

/**
 * Compatibility export. In browser/test environments this delegates to the
 * DOMParser sanitizer so the same ADR-002 allowlist is enforced.
 */
export function sanitizeSvgRegex(svgString: string): string {
  if (typeof DOMParser !== 'undefined' && typeof document !== 'undefined') {
    return sanitizeSvg(svgString)
  }

  return svgString
    .replace(/<\/?(?:script|foreignobject|iframe|embed|object|audio|video|image|a|style|animate|set|animatetransform|animatemotion|use|symbol|title|desc|textpath)\b[^>]*>/gi, '')
    .replace(/\s+on\w+\s*=\s*(?:"[^"]*"|'[^']*')/gi, '')
    .replace(/\s+(?:href|xlink:href|style)\s*=\s*(?:"[^"]*"|'[^']*')/gi, '')
    .replace(/url\(\s*['"]?(?!#[A-Za-z_][\w:.-]*['"]?\s*\))[^)]*\)/gi, '')
}

export default sanitizeSvg
