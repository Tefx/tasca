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
  'dx',
  'dy',
  'd',
  'points',
  'transform',
  'preserveaspectratio',
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
  'markerwidth',
  'markerheight',
  'markerunits',
  'refx',
  'refy',
  'orient',
])

const MARKER_REFERENCE_ATTRIBUTES = new Set(['marker-start', 'marker-mid', 'marker-end'])
const PAINT_ATTRIBUTES = new Set(['fill', 'stroke'])
const MARKER_NUMERIC_ATTRIBUTES = new Set(['markerwidth', 'markerheight', 'refx', 'refy'])

const EVENT_HANDLER_PATTERN = /^on/i
const INTERNAL_FRAGMENT_PATTERN = /^#[A-Za-z_][\w:.-]*$/
const INTERNAL_URL_FUNCTION_PATTERN = /^url\(\s*['"]?(#[A-Za-z_][\w:.-]*)['"]?\s*\)$/i
const URL_FUNCTION_PATTERN = /url\(/i
const FORBIDDEN_URL_PATTERN = /(?:javascript|data|file|https?|mailto):|\b(?:src|href)\s*=|@import/i
const RELATIVE_URL_LIKE_PATTERN = /^(?:\.?\.?\/|\/|[^\s]+\.(?:svg|png|jpg|jpeg|gif|webp)(?:[#?/].*)?)/i
const SVG_NUMBER_PATTERN = /^[-+]?(?:(?:\d+\.?\d*)|(?:\.\d+))(?:e[-+]?\d+)?$/i
const SAFE_TRANSFORM_FUNCTIONS = new Map<string, ReadonlySet<number>>([
  ['matrix', new Set([6])],
  ['translate', new Set([1, 2])],
  ['scale', new Set([1, 2])],
  ['rotate', new Set([1, 3])],
  ['skewx', new Set([1])],
  ['skewy', new Set([1])],
])
const MAX_TRANSFORM_LENGTH = 512
const MAX_ABSOLUTE_SVG_NUMBER = 1_000_000

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

    if (!isAllowedAttributeValue(element, attrName, attr.value)) {
      attributesToRemove.push(attr.name)
    }
  }

  for (const attrName of attributesToRemove) {
    element.removeAttribute(attrName)
  }
}

function isAllowedAttributeValue(element: Element, attrName: string, rawValue: string): boolean {
  const value = rawValue.trim()
  const tagName = element.tagName.toLowerCase()

  if (MARKER_REFERENCE_ATTRIBUTES.has(attrName)) {
    return INTERNAL_URL_FUNCTION_PATTERN.test(value)
  }

  if (PAINT_ATTRIBUTES.has(attrName)) {
    if (URL_FUNCTION_PATTERN.test(value)) {
      return INTERNAL_URL_FUNCTION_PATTERN.test(value)
    }

    return !FORBIDDEN_URL_PATTERN.test(value) && !RELATIVE_URL_LIKE_PATTERN.test(value)
  }

  if (attrName === 'transform') {
    return isAllowedTransformValue(value)
  }

  if (MARKER_NUMERIC_ATTRIBUTES.has(attrName)) {
    return tagName === 'marker' && isFiniteSvgNumber(value)
  }

  if (attrName === 'markerunits') {
    return tagName === 'marker' && (value === 'strokeWidth' || value === 'userSpaceOnUse')
  }

  if (attrName === 'orient') {
    return tagName === 'marker' && (value === 'auto' || value === 'auto-start-reverse' || isFiniteSvgNumber(value))
  }

  if (attrName === 'preserveaspectratio') {
    return tagName === 'svg' && isAllowedPreserveAspectRatioValue(value)
  }

  return !URL_FUNCTION_PATTERN.test(value) && !FORBIDDEN_URL_PATTERN.test(value)
}

function isFiniteSvgNumber(value: string): boolean {
  if (!SVG_NUMBER_PATTERN.test(value)) {
    return false
  }

  const parsed = Number(value)
  return Number.isFinite(parsed) && Math.abs(parsed) <= MAX_ABSOLUTE_SVG_NUMBER
}

function parseTransformArguments(args: string): string[] {
  return args
    .trim()
    .replace(/,/g, ' ')
    .split(/\s+/)
    .filter(Boolean)
}

function isAllowedTransformValue(value: string): boolean {
  if (value.length === 0 || value.length > MAX_TRANSFORM_LENGTH) {
    return false
  }

  let offset = 0
  const transformPattern = /([A-Za-z]+)\s*\(([^()]*)\)/gy

  while (offset < value.length) {
    while (offset < value.length && /[\s,]/.test(value[offset])) {
      offset += 1
    }

    if (offset >= value.length) {
      break
    }

    transformPattern.lastIndex = offset
    const match = transformPattern.exec(value)
    if (!match) {
      return false
    }

    const functionName = match[1].toLowerCase()
    const allowedArgCounts = SAFE_TRANSFORM_FUNCTIONS.get(functionName)
    if (!allowedArgCounts) {
      return false
    }

    const args = parseTransformArguments(match[2])
    if (!allowedArgCounts.has(args.length) || !args.every(isFiniteSvgNumber)) {
      return false
    }

    offset = transformPattern.lastIndex
  }

  return offset > 0
}

function isAllowedPreserveAspectRatioValue(value: string): boolean {
  if (value === 'none') {
    return true
  }

  const [align, meetOrSlice, extra] = value.split(/\s+/)
  if (extra !== undefined) {
    return false
  }

  const allowedAlignments = new Set([
    'xMinYMin',
    'xMidYMin',
    'xMaxYMin',
    'xMinYMid',
    'xMidYMid',
    'xMaxYMid',
    'xMinYMax',
    'xMidYMax',
    'xMaxYMax',
  ])

  return allowedAlignments.has(align) && (meetOrSlice === undefined || meetOrSlice === 'meet' || meetOrSlice === 'slice')
}

export function isSafeInternalFragmentReference(value: string): boolean {
  return INTERNAL_FRAGMENT_PATTERN.test(value.trim())
}

export function hasDangerousSvgContent(svgString: string): boolean {
  const dangerousPatterns = [
    /<(?:script|foreignobject|iframe|embed|object|audio|video|image|a|style|animate|set|animatetransform|animatemotion|use|symbol|title|desc|textpath)\b/i,
    /\bon\w+\s*=/i,
    /\b(?:href|xlink:href|style|target|rel|aria-labelledby|aria-describedby|tabindex|gradientUnits|gradientTransform|spreadMethod|fx|fy|offset|stop-color|stop-opacity)\s*=/i,
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
