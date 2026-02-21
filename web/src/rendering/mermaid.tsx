/**
 * Mermaid diagram rendering with security hardening.
 *
 * SECURITY: This module strips dangerous Mermaid directives before rendering.
 *
 * ## Security Measures
 *
 * - **Init directives stripped**: `%%{init: ...}%%` directives are removed
 *   to prevent configuration injection attacks (e.g., XSS, SSRF)
 * - **Additional directives**: `%%{initialize: ...}%%` also stripped (alias)
 *
 * ## References
 *
 * - ADR-001: Mermaid Rendering Strategy
 * - https://github.com/mermaid-js/mermaid/security/advisories/GHSA-7rqq-prvp-x9jh
 * - https://github.com/mermaid-js/mermaid/security/advisories/GHSA-8gwm-58g9-j8pw
 */

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
 * @note Mermaid rendering will be fully implemented when mermaid package
 * is added. This module provides the security sanitization layer.
 */
export interface MermaidRendererProps {
  /** Mermaid diagram code (will be sanitized before rendering) */
  code: string
  /** Optional CSS class name */
  className?: string
}

/**
 * Placeholder for Mermaid renderer component.
 *
 * SECURITY: When implemented, this component MUST:
 * 1. Call `stripMermaidInitDirectives()` on the code before rendering
 * 2. Sanitize the rendered SVG output (see ADR-002)
 * 3. Use a pinned Mermaid version (see ADR-001)
 *
 * @todo Implement full Mermaid rendering with mermaid package
 * @todo Add SVG sanitization per ADR-002
 */
export function MermaidRenderer({ code, className }: MermaidRendererProps): JSX.Element {
  // SECURITY: Always sanitize input code
  const sanitizedCode = stripMermaidInitDirectives(code)

  // Placeholder implementation - will be replaced with actual Mermaid rendering
  return (
    <div className={className}>
      <pre className="mermaid-placeholder">{sanitizedCode}</pre>
    </div>
  )
}

/**
 * Re-export sanitization functions for use in other modules.
 */
export default MermaidRenderer