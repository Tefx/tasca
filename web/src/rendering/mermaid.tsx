/**
 * Mermaid diagram rendering with security hardening.
 *
 * SECURITY: This module implements defense-in-depth for Mermaid diagrams:
 *
 * 1. **Mermaid initialization**: `mermaid.initialize({ securityLevel: 'strict' })`
 *    is called at module load time to prevent click/href handler evaluation
 * 2. **Input sanitization**: Init directives (`%%{init: ...}%%`) are stripped
 *    to prevent configuration injection attacks
 * 3. **Output sanitization**: SVG output is passed through `sanitizeSvg()`
 *    to remove dangerous elements and attributes
 *
 * ## Security Measures (ADR-001, ADR-002)
 *
 * - **Strict security level**: `mermaid.initialize({ securityLevel: 'strict' })`
 *   called at module load — not relying on library defaults
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
import mermaid, { type MermaidConfig } from 'mermaid'
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
 * References: ADR-001 (mandatory security guardrails)
 */
mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' })

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
 */
export function hasMermaidInitDirectives(code: string): boolean {
  return countMermaidInitDirectives(code) > 0
}

/**
 * Props for the MermaidRenderer component.
 *
 * @property code - Mermaid diagram code (will be sanitized before rendering)
 * @property className - Optional CSS class name for the container
 * @property config - Optional Mermaid configuration (securityLevel is always 'strict')
 */
export interface MermaidRendererProps {
  /** Mermaid diagram code (will be sanitized before rendering) */
  code: string
  /** Optional CSS class name */
  className?: string
  /** Optional Mermaid config (securityLevel will be overridden to 'strict') */
  config?: MermaidConfig
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

      // SECURITY LAYER 3: Render the diagram
      const { svg: rawSvg } = await mermaid.render(diagramId, sanitizedCode)

      // SECURITY LAYER 4: Sanitize the SVG output before DOM injection
      // This removes script tags, event handlers, and other dangerous content
      const sanitizedSvg = sanitizeSvg(rawSvg)

      setSvgContent(sanitizedSvg)
      setRenderState('success')
    } catch (err) {
      // Handle rendering errors gracefully
      const errorMessage = err instanceof Error ? err.message : 'Failed to render diagram'
      console.error('Mermaid rendering error:', err)
      setError(errorMessage)
      setRenderState('error')
      setSvgContent(null)
    }
  }, [code])

  // Render diagram when code changes
  useEffect(() => {
    renderDiagram()
  }, [renderDiagram])

  // Handle rendering states
  if (renderState === 'rendering') {
    return (
      <div className={className} role="status" aria-label="Loading diagram">
        <div className="flex items-center justify-center p-4 text-muted-foreground">
          <span className="animate-pulse">Rendering diagram...</span>
        </div>
      </div>
    )
  }

  if (renderState === 'error') {
    return (
      <div className={className} role="alert">
        <div className="border border-destructive/50 rounded-lg p-4 text-destructive">
          <p className="font-medium">Failed to render diagram</p>
          <p className="text-sm mt-1 opacity-80">{error}</p>
          <pre className="mt-2 text-xs bg-muted p-2 rounded overflow-x-auto">{code}</pre>
        </div>
      </div>
    )
  }

  if (!svgContent) {
    return (
      <div className={className}>
        <div className="text-muted-foreground p-4">No diagram to display</div>
      </div>
    )
  }

  // SECURITY: svgContent has been sanitized by sanitizeSvg()
  // It's safe to use dangerouslySetInnerHTML here because:
  // 1. It came from mermaid.render(), not arbitrary user input
  // 2. It was filtered through sanitizeSvg() which removes dangerous elements
  // 3. Input was pre-sanitized by stripMermaidInitDirectives()
  return (
    <div
      ref={containerRef}
      className={className}
      dangerouslySetInnerHTML={{ __html: svgContent }}
      aria-label="Mermaid diagram"
      role="img"
    />
  )
}

/**
 * Re-export sanitization functions for use in other modules.
 */
export default MermaidRenderer