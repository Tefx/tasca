/**
 * Mermaid diagram rendering with security hardening.
 *
 * SECURITY: This module implements defense-in-depth for Mermaid diagrams:
 *
 * 1. **Mermaid initialization**: `mermaid.initialize({ securityLevel: 'strict',
 *    flowchart: { htmlLabels: false } })` is called at module load time to
 *    prevent click/href handler evaluation and avoid `<foreignObject>` labels
 * 2. **Input sanitization**: Init directives (`%%{init: ...}%%`) are stripped
 *    to prevent configuration injection attacks
 * 3. **Output sanitization**: SVG output is passed through `sanitizeSvg()`
 *    to remove dangerous elements and attributes
 *
 * ## Security Measures (ADR-001, ADR-002)
 *
 * - **Strict security level**: `mermaid.initialize({ securityLevel: 'strict' })`
 *   called at module load — not relying on library defaults
 * - **SVG labels**: `flowchart.htmlLabels: false` keeps labels in SVG text
 *   elements so ADR-002 can forbid `<foreignObject>` without dropping labels
 * - **Init directives stripped**: `%%{init: ...}%%` directives are removed
 *   to prevent configuration injection attacks (e.g., XSS, SSRF)
 * - **Additional directives**: `%%{initialize: ...}%%` also stripped (alias)
 * - **SVG sanitization**: Output SVG is sanitized before DOM injection
 *   (removes script tags, event handlers, external references)
 *
 * ## References
 *
 * - ADR-001: Mermaid Rendering Strategy (pinned version, secure defaults)
 * - ADR-002: SVG Sanitization Strategy (element/attribute allowlists)
 * - https://github.com/mermaid-js/mermaid/security/advisories/GHSA-7rqq-prvp-x9jh
 * - https://github.com/mermaid-js/mermaid/security/advisories/GHSA-8gwm-58g9-j8pw
 */

import { useEffect, useRef, useState, useCallback } from 'react'
import mermaid from 'mermaid'
import { sanitizeSvg } from './svg-sanitizer'

// =============================================================================
// Security initialization (ADR-001: mandatory secure defaults)
// =============================================================================

/**
 * Initialize Mermaid with strict security settings.
 *
 * SECURITY: `securityLevel: 'strict'` prevents Mermaid from evaluating
 * click/href handlers and other interactive features that could be abused
 * for XSS. This call is required — do not rely on library defaults.
 *
 * `startOnLoad: false` prevents Mermaid from auto-scanning the DOM on
 * import, which could process unsanitized content before our sanitization
 * pipeline runs.
 *
 * `flowchart.htmlLabels: false` avoids generated `<foreignObject>` labels.
 * ADR-002 forbids `<foreignObject>`, so keeping labels as SVG text preserves
 * diagram readability after sanitization.
 *
 * References: ADR-001 (mandatory security guardrails)
 */
mermaid.initialize({
  startOnLoad: false,
  securityLevel: 'strict',
  flowchart: { htmlLabels: false },
})

// =============================================================================
// Constants
// =============================================================================

/**
 * Unique ID counter for Mermaid diagram instances.
 */
let diagramIdCounter = 0

/**
 * Generate a unique ID for a Mermaid diagram.
 * @returns Unique diagram ID
 */
function generateDiagramId(): string {
  return `mermaid-diagram-${++diagramIdCounter}`
}

// =============================================================================
// Input Sanitization
// =============================================================================

/**
 * Regex pattern to match Mermaid init directives.
 *
 * Matches:
 * - `%%{init: {...}}%%` - standard init directive
 * - `%%{initialize: {...}}%%` - alternative spelling
 *
 * The pattern handles:
 * - Multi-line content inside the directive
 * - Nested braces (with some limitations)
 * - Whitespace variations
 *
 * @see https://mermaid.js.org/config/theming.html#directive
 */
const MERMAID_INIT_DIRECTIVE_PATTERN = /%%\s*\{\s*(init|initialize)\s*:\s*\{[\s\S]*?\}\s*\}\s*%%/g

/**
 * Strip Mermaid init directives from diagram code.
 *
 * Init directives can be abused for XSS and other injection attacks
 * by allowing attackers to configure the Mermaid renderer with
 * malicious themes, fonts, or other settings.
 *
 * @param code - Raw Mermaid diagram code
 * @returns Sanitized code with init directives removed
 *
 * @example
 * ```typescript
 * const malicious = '%%{init: {"theme": "dark"}}%%\ngraph TD; A-->B'
 * const safe = stripMermaidInitDirectives(malicious)
 * // Returns: '\ngraph TD; A-->B'
 * ```
 *
 * @example
 * ```typescript
 * const multi = '%%{init: {...}}%%%%{initialize: {...}}%%graph TD'
 * const safe = stripMermaidInitDirectives(multi)
 * // Returns: 'graph TD' (both directives stripped)
 * ```
 */
export function stripMermaidInitDirectives(code: string): string {
  return code.replace(MERMAID_INIT_DIRECTIVE_PATTERN, '')
}

/**
 * Count the number of init directives in Mermaid code.
 *
 * Useful for testing and logging.
 *
 * @param code - Mermaid diagram code
 * @returns Number of init directives found
 *
 * @example
 * ```typescript
 * countMermaidInitDirectives('graph TD; A-->B') // 0
 * countMermaidInitDirectives('%%{init: {}}%%\ngraph TD') // 1
 * ```
 */
export function countMermaidInitDirectives(code: string): number {
  const matches = code.match(MERMAID_INIT_DIRECTIVE_PATTERN)
  return matches ? matches.length : 0
}

/**
 * Check if Mermaid code contains any init directives.
 *
 * @param code - Mermaid diagram code
 * @returns True if init directives are present
 *
 * @example
 * ```typescript
 * hasMermaidInitDirectives('graph TD; A-->B') // false
 * hasMermaidInitDirectives('%%{init: {}}%%\ngraph TD') // true
 * ```
 */
export function hasMermaidInitDirectives(code: string): boolean {
  return countMermaidInitDirectives(code) > 0
}

// =============================================================================
// Safe presentation repair
// =============================================================================

/**
 * Post-sanitization presentation attributes for Mermaid diagrams in the dark
 * Mission Control stream.
 *
 * ADR-002 forbids Mermaid's generated `<style>` blocks and inline `style="..."`
 * attributes. Without replacement presentation, SVG shapes fall back to the
 * browser default black fill/stroke, which renders as unreadable black blocks
 * on the dark stream background. These fixed values use only ADR-002-allowed
 * presentation attributes (`fill`, `stroke`, `stroke-width`, `fill-opacity`).
 */
const READABLE_MERMAID_PRESENTATION = {
  nodeFill: '#1e3a5f',
  nodeStroke: '#93c5fd',
  clusterFill: '#111827',
  clusterStroke: '#64748b',
  edgeStroke: '#94a3b8',
  labelFill: '#0f172a',
  textFill: '#f8fafc',
} as const

const MERMAID_NODE_SHAPE_SELECTOR = [
  '.node rect',
  '.node circle',
  '.node ellipse',
  '.node polygon',
  '.node path',
  'rect.label-container',
  'rect.actor',
  'rect.activation0',
  'rect.activation1',
  'rect.activation2',
  'rect.note',
  'polygon.note',
].join(', ')

const MERMAID_CLUSTER_SHAPE_SELECTOR = [
  '.cluster rect',
  '.cluster polygon',
  '.cluster path',
].join(', ')

const MERMAID_EDGE_SELECTOR = [
  '.flowchart-link',
  '.edgePath path',
  'path.path',
  '.messageLine0',
  '.messageLine1',
  '.loopLine',
  '.actor-line',
].join(', ')

const MERMAID_LABEL_BACKGROUND_SELECTOR = [
  '.edgeLabel rect',
  'rect.labelBkg',
  'rect.labelBox',
].join(', ')

const MERMAID_TEXT_SELECTOR = [
  'text',
  'tspan',
  '.nodeLabel',
  '.edgeLabel',
  '.cluster-label',
  '.messageText',
  '.noteText',
  '.loopText',
].join(', ')

function setPresentationAttributes(
  elements: NodeListOf<Element>,
  attributes: Readonly<Record<string, string>>
): void {
  for (const element of Array.from(elements)) {
    for (const [name, value] of Object.entries(attributes)) {
      element.setAttribute(name, value)
    }
  }
}

/**
 * Add safe, explicit presentation attributes to sanitized Mermaid SVG output.
 *
 * @param svgString - Sanitized Mermaid SVG string
 * @returns SVG string with readable dark-theme presentation attributes
 */
export function applyReadableMermaidTheme(svgString: string): string {
  const parsed = new DOMParser().parseFromString(svgString, 'image/svg+xml')
  if (parsed.querySelector('parsererror')) {
    return svgString
  }

  const svg = parsed.documentElement
  if (svg.tagName.toLowerCase() !== 'svg') {
    return svgString
  }

  setPresentationAttributes(svg.querySelectorAll(MERMAID_NODE_SHAPE_SELECTOR), {
    fill: READABLE_MERMAID_PRESENTATION.nodeFill,
    stroke: READABLE_MERMAID_PRESENTATION.nodeStroke,
    'stroke-width': '1.5',
  })
  setPresentationAttributes(svg.querySelectorAll(MERMAID_CLUSTER_SHAPE_SELECTOR), {
    fill: READABLE_MERMAID_PRESENTATION.clusterFill,
    stroke: READABLE_MERMAID_PRESENTATION.clusterStroke,
    'stroke-width': '1.5',
  })
  setPresentationAttributes(svg.querySelectorAll(MERMAID_EDGE_SELECTOR), {
    fill: 'none',
    stroke: READABLE_MERMAID_PRESENTATION.edgeStroke,
    'stroke-width': '1.5',
  })
  setPresentationAttributes(svg.querySelectorAll('marker path'), {
    fill: READABLE_MERMAID_PRESENTATION.edgeStroke,
    stroke: READABLE_MERMAID_PRESENTATION.edgeStroke,
  })
  setPresentationAttributes(svg.querySelectorAll(MERMAID_LABEL_BACKGROUND_SELECTOR), {
    fill: READABLE_MERMAID_PRESENTATION.labelFill,
    'fill-opacity': '0.95',
  })
  setPresentationAttributes(svg.querySelectorAll(MERMAID_TEXT_SELECTOR), {
    fill: READABLE_MERMAID_PRESENTATION.textFill,
  })

  return new XMLSerializer().serializeToString(svg)
}

/**
 * Props for the MermaidRenderer component.
 *
 * @property code - Mermaid diagram code (will be sanitized before rendering)
 * @property className - Optional CSS class name for the container
 */
export interface MermaidRendererProps {
  /** Mermaid diagram code (will be sanitized before rendering) */
  code: string
  /** Optional CSS class name */
  className?: string
}

/**
 * Render state for tracking rendering progress.
 */
type RenderState = 'idle' | 'rendering' | 'success' | 'error'

/**
 * Mermaid renderer component with security hardening.
 *
 * SECURITY: This component implements a multi-layer defense:
 *
 * 1. **Mermaid initialization**: `securityLevel: 'strict'` enforced via
 *    `mermaid.initialize()` at module load time (see top of file)
 * 2. **Input sanitization**: Init directives are stripped from the code
 *    before rendering (see `stripMermaidInitDirectives`)
 * 3. **Output sanitization**: SVG output is passed through `sanitizeSvg()`
 *    before being injected into the DOM
 *
 * @param props - Component props
 * @returns React component that renders the sanitized Mermaid diagram
 *
 * @example
 * ```tsx
 * <MermaidRenderer code="graph TD; A-->B" />
 * ```
 *
 * @example
 * ```tsx
 * // With custom styling
 * <MermaidRenderer
 *   code="flowchart LR; A-->B"
 *   className="border rounded-lg p-4"
 * />
 * ```
 */
export function MermaidRenderer({ code, className }: MermaidRendererProps): JSX.Element {
  // Ref for the container element
  const containerRef = useRef<HTMLDivElement>(null)

  // State for tracking rendering
  const [renderState, setRenderState] = useState<RenderState>('idle')
  const [error, setError] = useState<string | null>(null)
  const [svgContent, setSvgContent] = useState<string | null>(null)

  /**
   * Render the Mermaid diagram.
   *
   * SECURITY FLOW:
   * 1. Strip init directives from input code
   * 2. Call mermaid.render() to generate SVG
   * 3. Pass SVG through sanitizeSvg() before DOM injection
   */
  const renderDiagram = useCallback(async () => {
    if (!code) {
      setSvgContent(null)
      setRenderState('idle')
      return
    }

    setRenderState('rendering')
    setError(null)

    try {
      // SECURITY LAYER 1: Strip init directives from input
      const sanitizedCode = stripMermaidInitDirectives(code)

      // Generate unique ID for this diagram
      const diagramId = generateDiagramId()

      // SECURITY LAYER 2: mermaid.initialize({ securityLevel: 'strict' }) is called
      // at module load time (see top of file). All renders in this session use strict mode.

      // SECURITY LAYER 3: Render the diagram. Passing an explicit temporary
      // container prevents Mermaid from leaving raw SVG output directly in
      // document.body while we still need to sanitize it. Mermaid measures this
      // container, so it must be attached to the DOM during render.
      const renderContainer = document.createElement('div')
      renderContainer.setAttribute('aria-hidden', 'true')
      renderContainer.style.position = 'absolute'
      renderContainer.style.left = '-10000px'
      renderContainer.style.top = '0'
      document.body.appendChild(renderContainer)

      let rawSvg = ''
      try {
        const renderResult = await mermaid.render(diagramId, sanitizedCode, renderContainer)
        rawSvg = renderResult.svg
      } finally {
        renderContainer.remove()
      }

      // SECURITY LAYER 4: Sanitize the SVG output before DOM injection
      // This removes script tags, event handlers, and other dangerous content
      const sanitizedSvg = sanitizeSvg(rawSvg)
      const readableSvg = applyReadableMermaidTheme(sanitizedSvg)

      setSvgContent(readableSvg)
      setRenderState('success')
    } catch (err) {
      // Handle author-supplied Mermaid syntax errors gracefully. These are
      // expected for LLM/user-generated diagrams, so avoid console.error spam;
      // the visible fallback below preserves the source for diagnosis/editing.
      const errorMessage = err instanceof Error ? err.message : 'Failed to render diagram'
      setError(errorMessage)
      setRenderState('error')
      setSvgContent(null)
    }
  }, [code])

  // Render diagram when code changes
  useEffect(() => {
    renderDiagram()
  }, [renderDiagram])

  useEffect(() => {
    const container = containerRef.current
    if (!container || !svgContent) {
      return
    }

    const parsed = new DOMParser().parseFromString(svgContent, 'image/svg+xml')
    const svg = parsed.documentElement
    if (parsed.querySelector('parsererror') || svg.tagName.toLowerCase() !== 'svg') {
      container.replaceChildren()
      return
    }

    const importedSvg = document.importNode(svg, true)
    container.replaceChildren(importedSvg)
  }, [svgContent])

  // Handle rendering states
  if (renderState === 'rendering') {
    return (
      <div className={className} role="status" aria-label="Mermaid diagram">
        <div className="flex items-center justify-center p-4 text-muted-foreground">
          <span className="animate-pulse">Rendering diagram...</span>
        </div>
      </div>
    )
  }

  if (renderState === 'error') {
    return (
      <div
        className={`${className ?? ''} mc-mermaid-diagram--error`.trim()}
        aria-label="Mermaid diagram render failed"
      >
        <div className="mc-mermaid-error-message" role="alert">
          <p className="mc-mermaid-error-title">Failed to render Mermaid diagram</p>
          <p className="mc-mermaid-error-detail">{error}</p>
        </div>
        <pre className="mc-mermaid-source" aria-label="Mermaid source fallback">
          <code className="language-mermaid">{code}</code>
        </pre>
      </div>
    )
  }

  if (!svgContent) {
    return (
      <div className={className} aria-label="Mermaid diagram">
        <div className="text-muted-foreground p-4">No diagram to display</div>
      </div>
    )
  }

  // SECURITY: svgContent has been sanitized by sanitizeSvg() and is imported
  // via DOMParser/document.importNode in the lifecycle effect above. This avoids
  // injecting SVG strings with innerHTML/dangerouslySetInnerHTML per ADR-002 §5.
  return (
    <div
      ref={containerRef}
      className={className}
      aria-label="Mermaid diagram"
      role="img"
    />
  )
}

/**
 * Re-export sanitization functions for use in other modules.
 */
export default MermaidRenderer
