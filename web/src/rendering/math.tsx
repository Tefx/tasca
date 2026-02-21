/**
 * Math rendering utilities.
 *
 * Renders LaTeX math expressions using KaTeX with security hardening.
 *
 * Security Configuration:
 * - trust: false - Blocks all potentially dangerous commands
 * - Custom allowlist - Only permits safe math commands
 *
 * Blocked commands include:
 * - \includegraphics - Could load external resources
 * - \input - Could read local files
 * - \write - Could write files
 * - \immediate - Could execute commands
 * - \openout - File system access
 * - \closeout - File system access
 * - \read - File system access
 *
 * @see https://katex.org/docs/options.html
 */
import katex, { type KatexOptions } from 'katex'
import 'katex/dist/katex.min.css'

/**
 * Allowlist of safe LaTeX commands that are permitted in math expressions.
 * These commands are purely mathematical and cannot access external resources
 * or execute code.
 */
const SAFE_MATH_COMMANDS = new Set([
  // Basic math operations
  'frac', 'sqrt', 'root', 'cubicroot', 'fourthroot',
  
  // Subscripts and superscripts
  'subscript', 'superscript', 'sideset',
  
  // Brackets and delimiters
  'left', 'right', 'middle', 'big', 'Big', 'bigg', 'Bigg',
  'bigl', 'Bigl', 'biggl', 'Biggl',
  'bigr', 'Bigr', 'biggr', 'Biggr',
  'langle', 'rangle', 'lceil', 'rceil', 'lfloor', 'rfloor',
  
  // Greek letters
  'alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta', 'eta', 'theta',
  'iota', 'kappa', 'lambda', 'mu', 'nu', 'xi', 'omicron', 'pi', 'rho',
  'sigma', 'tau', 'upsilon', 'phi', 'chi', 'psi', 'omega',
  'Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon', 'Zeta', 'Eta', 'Theta',
  'Iota', 'Kappa', 'Lambda', 'Mu', 'Nu', 'Xi', 'Omicron', 'Pi', 'Rho',
  'Sigma', 'Tau', 'Upsilon', 'Phi', 'Chi', 'Psi', 'Omega',
  
  // Operators and symbols
  'sum', 'prod', 'coprod', 'int', 'iint', 'iiint', 'oint',
  'lim', 'limsup', 'liminf', 'sup', 'inf', 'max', 'min',
  'arg', 'argmax', 'argmin', 'det', 'gcd', 'lcm', 'dim', 'ker',
  'log', 'ln', 'lg', 'exp', 'sin', 'cos', 'tan', 'cot', 'sec', 'csc',
  'sinh', 'cosh', 'tanh', 'coth', 'arcsin', 'arccos', 'arctan',
  'sin^{-1}', 'cos^{-1}', 'tan^{-1}',
  
  // Fonts
  'mathrm', 'mathbf', 'mathit', 'mathbb', 'mathcal', 'mathsf', 'mathtt',
  'textrm', 'textbf', 'textit', 'textsf', 'texttt', 'text',
  'Bbb', 'bf', 'it', 'rm', 'sf', 'tt', 'cal',
  
  // Accents
  'hat', 'tilde', 'bar', 'vec', 'dot', 'ddot', 'acute', 'grave',
  'breve', 'check', 'widehat', 'widetilde', 'overline', 'underline',
  'overrightarrow', 'overleftarrow', 'overleftrightarrow',
  'overbrace', 'underbrace', 'overparen', 'underparen',
  
  // Spacing
  'quad', 'qquad', 'thinspace', 'negthinspace', 'hspace', 'kern',
  '!', ' ', ',', ';', ':',
  
  // Matrices
  'matrix', 'pmatrix', 'bmatrix', 'Bmatrix', 'vmatrix', 'Vmatrix',
  'array', 'cases', 'aligned', 'alignedat', 'gathered', 'split',
  
  // Fractions and related
  'dfrac', 'tfrac', 'cfrac', 'genfrac', 'above', 'abovewithdelims',
  'atop', 'atopwithdelims', 'over', 'overwithdelims', 'brace', 'brack',
  
  // Symbols
  'cdot', 'times', 'div', 'pm', 'mp', 'approx', 'equiv', 'cong', 'sim',
  'simeq', 'propto', 'neq', 'ne', 'leq', 'geq', 'll', 'gg', 'subset',
  'supset', 'subseteq', 'supseteq', 'sqsubseteq', 'sqsupseteq',
  'in', 'notin', 'ni', 'owns', 'cap', 'cup', 'setminus', 'emptyset',
  'forall', 'exists', 'nexists', 'therefore', 'because',
  'to', 'rightarrow', 'leftarrow', 'leftrightarrow', 'Rightarrow',
  'Leftarrow', 'Leftrightarrow', 'mapsto', 'longmapsto',
  'implies', 'iff', 'iffalse',
  
  // Misc
  'binom', 'binom', 'dbinom', 'tbinom',
  'color', 'colorbox', 'fcolorbox', 'pagecolor',
  'phantom', 'hphantom', 'vphantom',
  'smash', 'vdots', 'ddots', 'iddots', 'dots', 'ldots', 'cdots',
  'infty', 'partial', 'nabla', 'hbar', 'ell',
])

/**
 * Command categories that could be dangerous and should be blocked.
 * Used for error messages and security logging.
 */
const BLOCKED_COMMAND_CATEGORIES = {
  FILE_ACCESS: ['input', 'output', 'openin', 'openout', 'closein', 'closeout', 'read', 'write'],
  EXTERNAL_RESOURCES: ['includegraphics', 'includesvg', 'includevideo', 'url', 'href'],
  CODE_EXECUTION: ['immediate', 'write18', 'shell_escape', 'inputlineno'],
  SYSTEM_INFO: ['jobname', 'year', 'month', 'day', 'time', 'pdforrexec'],
}

/**
 * Check if a LaTeX command is in our safe allowlist.
 * 
 * @param command - The LaTeX command name (without backslash)
 * @returns true if the command is safe and allowed
 */
export function isCommandAllowed(command: string): boolean {
  return SAFE_MATH_COMMANDS.has(command)
}

/**
 * Check if a command is explicitly dangerous (for error reporting).
 * 
 * @param command - The LaTeX command name (without backslash)
 * @returns The category of the blocked command, or null if not categorized
 */
export function getBlockedCommandCategory(command: string): string | null {
  for (const [category, commands] of Object.entries(BLOCKED_COMMAND_CATEGORIES)) {
    if (commands.includes(command)) {
      return category
    }
  }
  return null
}

/**
 * Trust function for KaTeX that implements our allowlist security model.
 * 
 * This function is called by KaTeX for every command that could potentially
 * be dangerous. We check if the command is in our safe allowlist.
 * 
 * @param context - The trust context provided by KaTeX
 * @returns true if the command should be trusted, false otherwise
 * 
 * @see https://katex.org/docs/options.html -- trust option
 */
function trustHandler(context: { command: string }): boolean {
  const { command } = context
  
  // Check if command is in our allowlist
  if (isCommandAllowed(command)) {
    return true
  }
  
  // Log blocked command for debugging (in development)
  if (process.env.NODE_ENV === 'development') {
    const category = getBlockedCommandCategory(command)
    if (category) {
      // eslint-disable-next-line no-console
      console.warn(
        `[KaTeX Security] Blocked command '\\${command}' (${category} category)`
      )
    } else {
      // eslint-disable-next-line no-console
      console.warn(
        `[KaTeX Security] Blocked unknown command '\\${command}' (not in allowlist)`
      )
    }
  }
  
  return false
}

/**
 * Default KaTeX options with security hardening.
 */
export const SECURE_KATEX_OPTIONS: KatexOptions = {
  trust: trustHandler,
  strict: true, // Enable strict mode for better error messages
  throwOnError: false, // Don't throw on parse errors, show error message instead
  displayMode: false, // Default to inline mode
  errorColor: '#cc0000', // Error message color
  macros: {}, // No user-defined macros by default
  fleqn: false, // Left-align equations
  leqno: false, // Left-align equation numbers
  output: 'html', // Output format
}

/**
 * Render LaTeX math expression to HTML string.
 * 
 * Uses KaTeX with security hardening:
 * - trust: false by default, with our custom allowlist handler
 * - Only allows safe mathematical commands
 * - Blocks file access, external resources, and code execution
 * 
 * @param latex - The LaTeX math expression to render
 * @param options - Optional KaTeX options (will be merged with security defaults)
 * @returns The rendered HTML string
 * @throws Error if latex is empty or malformed
 * 
 * @example
 * ```tsx
 * const html = renderMathToString('E = mc^2')
 * // Returns: '<span class="katex">...</span>'
 * 
 * const displayHtml = renderMathToString('\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}', { displayMode: true })
 * ```
 */
export function renderMathToString(
  latex: string,
  options?: Partial<KatexOptions>
): string {
  if (!latex || typeof latex !== 'string') {
    throw new Error('LaTeX expression must be a non-empty string')
  }
  
  const mergedOptions: KatexOptions = {
    ...SECURE_KATEX_OPTIONS,
    ...options,
    // Always use our trust handler - override any provided trust option
    trust: trustHandler,
  }
  
  return katex.renderToString(latex, mergedOptions)
}

/**
 * Props for MathRenderer component.
 */
export interface MathRendererProps {
  /** The LaTeX math expression to render */
  latex: string
  /** Whether to render in display mode (block) vs inline mode */
  displayMode?: boolean
  /** Additional CSS class names */
  className?: string
  /** Error callback - called when LaTeX parsing fails */
  onError?: (error: Error) => void
}

/**
 * React component for rendering LaTeX math expressions.
 * 
 * Uses KaTeX with security hardening. Automatically handles errors
 * by displaying an error message inline (not throwing).
 * 
 * @example
 * ```tsx
 * // Inline math
 * <MathRenderer latex="E = mc^2" />
 * 
 * // Display mode (block)
 * <MathRenderer 
 *   latex="\sum_{i=1}^{n} i = \frac{n(n+1)}{2}" 
 *   displayMode 
 * />
 * ```
 */
export function MathRenderer({
  latex,
  displayMode = false,
  className,
  onError,
}: MathRendererProps): JSX.Element | null {
  if (!latex || typeof latex !== 'string') {
    return null
  }
  
  try {
    const html = renderMathToString(latex, { displayMode })
    
    return (
      <span
        className={className}
        dangerouslySetInnerHTML={{ __html: html }}
        aria-label={`Math: ${latex}`}
        role="math"
      />
    )
  } catch (error) {
    // Report error to callback
    if (onError && error instanceof Error) {
      onError(error)
    }
    
    // Display error message to user
    return (
      <span
        className={className}
        style={{ color: SECURE_KATEX_OPTIONS.errorColor }}
        aria-label={`Math error: ${latex}`}
      >
        [Math Error: {latex}]
      </span>
    )
  }
}

/**
 * Hook to render math and get the result/error separately.
 * Useful for testing and programmatic use.
 * 
 * @param latex - The LaTeX math expression to render
 * @param options - Optional KaTeX options
 * @returns Object with rendered HTML or error
 */
export function useMathRenderer(
  latex: string,
  options?: Partial<KatexOptions>
): { html: string | null; error: Error | null } {
  if (!latex || typeof latex !== 'string') {
    return { html: null, error: null }
  }
  
  try {
    const html = renderMathToString(latex, options)
    return { html, error: null }
  } catch (error) {
    return { 
      html: null, 
      error: error instanceof Error ? error : new Error(String(error)) 
    }
  }
}

// Re-export types for external use
export type { KatexOptions }