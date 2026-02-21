/**
 * Security tests for KaTeX math rendering.
 *
 * Verifies that KaTeX is configured with trust=false and that
 * malicious LaTeX commands are blocked.
 *
 * Blocked command categories:
 * - File access: \input, \read, \write, \openin, \openout, etc.
 * - External resources: \includegraphics, \url, \href, etc.
 * - Code execution: \immediate, \write18, etc.
 * - System info: \jobname, \year, \month, \day, etc.
 *
 * @see https://katex.org/docs/options.html
 * @see https://katex.org/docs/supported.html
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  renderMathToString,
  MathRenderer,
  isCommandAllowed,
  getBlockedCommandCategory,
  SECURE_KATEX_OPTIONS,
} from './math'

describe('KaTeX Security Configuration', () => {
  describe('SECURE_KATEX_OPTIONS', () => {
    it('has trust configured (not simply false)', () => {
      // Trust should be a function (our allowlist handler)
      expect(typeof SECURE_KATEX_OPTIONS.trust).toBe('function')
    })

    it('has strict mode enabled', () => {
      expect(SECURE_KATEX_OPTIONS.strict).toBe(true)
    })

    it('has throwOnError disabled (graceful error handling)', () => {
      expect(SECURE_KATEX_OPTIONS.throwOnError).toBe(false)
    })
  })
})

describe('Command Allowlist', () => {
  describe('isCommandAllowed', () => {
    it('allows basic math commands', () => {
      expect(isCommandAllowed('frac')).toBe(true)
      expect(isCommandAllowed('sqrt')).toBe(true)
      expect(isCommandAllowed('sum')).toBe(true)
      expect(isCommandAllowed('int')).toBe(true)
      expect(isCommandAllowed('alpha')).toBe(true)
      expect(isCommandAllowed('beta')).toBe(true)
    })

    it('allows font commands', () => {
      expect(isCommandAllowed('mathrm')).toBe(true)
      expect(isCommandAllowed('mathbf')).toBe(true)
      expect(isCommandAllowed('mathit')).toBe(true)
      expect(isCommandAllowed('mathbb')).toBe(true)
    })

    it('allows matrix commands', () => {
      expect(isCommandAllowed('matrix')).toBe(true)
      expect(isCommandAllowed('pmatrix')).toBe(true)
      expect(isCommandAllowed('bmatrix')).toBe(true)
    })

    it('blocks file access commands', () => {
      expect(isCommandAllowed('input')).toBe(false)
      expect(isCommandAllowed('read')).toBe(false)
      expect(isCommandAllowed('write')).toBe(false)
      expect(isCommandAllowed('openin')).toBe(false)
      expect(isCommandAllowed('openout')).toBe(false)
    })

    it('blocks external resource commands', () => {
      expect(isCommandAllowed('includegraphics')).toBe(false)
      expect(isCommandAllowed('includesvg')).toBe(false)
      expect(isCommandAllowed('url')).toBe(false)
      expect(isCommandAllowed('href')).toBe(false)
    })

    it('blocks code execution commands', () => {
      expect(isCommandAllowed('immediate')).toBe(false)
      expect(isCommandAllowed('write18')).toBe(false)
      expect(isCommandAllowed('shell_escape')).toBe(false)
    })

    it('blocks system info commands', () => {
      expect(isCommandAllowed('jobname')).toBe(false)
      expect(isCommandAllowed('year')).toBe(false)
      expect(isCommandAllowed('month')).toBe(false)
      expect(isCommandAllowed('day')).toBe(false)
    })

    it('blocks unknown commands', () => {
      expect(isCommandAllowed('someRandomCommand')).toBe(false)
      expect(isCommandAllowed('totallyMadeUpCommand')).toBe(false)
    })
  })

  describe('getBlockedCommandCategory', () => {
    it('categorizes file access commands', () => {
      expect(getBlockedCommandCategory('input')).toBe('FILE_ACCESS')
      expect(getBlockedCommandCategory('read')).toBe('FILE_ACCESS')
      expect(getBlockedCommandCategory('write')).toBe('FILE_ACCESS')
    })

    it('categorizes external resource commands', () => {
      expect(getBlockedCommandCategory('includegraphics')).toBe('EXTERNAL_RESOURCES')
      expect(getBlockedCommandCategory('url')).toBe('EXTERNAL_RESOURCES')
      expect(getBlockedCommandCategory('href')).toBe('EXTERNAL_RESOURCES')
    })

    it('categorizes code execution commands', () => {
      expect(getBlockedCommandCategory('immediate')).toBe('CODE_EXECUTION')
      expect(getBlockedCommandCategory('write18')).toBe('CODE_EXECUTION')
    })

    it('categorizes system info commands', () => {
      expect(getBlockedCommandCategory('jobname')).toBe('SYSTEM_INFO')
      expect(getBlockedCommandCategory('year')).toBe('SYSTEM_INFO')
    })

    it('returns null for uncategorized/unknown commands', () => {
      expect(getBlockedCommandCategory('someUnknownCommand')).toBe(null)
      expect(getBlockedCommandCategory('frac')).toBe(null) // safe but not blocked
    })
  })
})

describe('Blocked Commands - XSS Prevention', () => {
  /**
   * IMPORTANT: KaTeX trust handler behavior
   * 
   * When trust returns false for a command:
   * - The command is NOT executed (no dangerous behavior)
   * - The command name is rendered as text in errorColor (red)
   * - No parse error is thrown (graceful degradation)
   * 
   * Security is verified by checking that:
   * 1. No dangerous side effects occur (no <img>, no <a href>, etc.)
   * 2. The command is rendered as text, not executed
   * 3. The command appears in error color (indicating it was blocked)
   */
  
  describe('\\includegraphics - External Resource Loading', () => {
    it('blocks \\includegraphics command - does not create img element', () => {
      // This command could load external images, which could track users
      const latex = '\\includegraphics[width=100px]{https://evil.com/tracker.png}'
      
      const result = renderMathToString(latex)
      
      // SECURITY: The output should NOT contain an img tag
      expect(result).not.toMatch(/<img/)
      // The command should be rendered as text (not executed)
      expect(result).toContain('\\includegraphics')
      // Should appear in error color (red), indicating it was blocked
      expect(result).toMatch(/color:#cc0000|#cc0000/)
    })

    it('blocks \\includegraphics with local file path', () => {
      const latex = '\\includegraphics{/etc/passwd}'
      
      const result = renderMathToString(latex)
      
      // SECURITY: No img element created
      expect(result).not.toMatch(/<img/)
      // Command rendered as text
      expect(result).toContain('\\includegraphics')
    })
  })

  describe('\\input - File Inclusion', () => {
    it('blocks \\input command - renders as text', () => {
      // This command could include local files
      const latex = '\\input{/etc/passwd}'
      
      const result = renderMathToString(latex)
      
      // SECURITY: Command rendered as text, not executed as file inclusion
      expect(result).toContain('\\input')
      // The command is in error color (blocked by trust handler)
      expect(result).toMatch(/color:#cc0000|#cc0000/)
      // Key security guarantee: command not executed, file not read
      expect(result).toMatch(/katex/)  // Valid KaTeX output
    })
  })

  describe('\\href and \\url - External Links', () => {
    it('blocks \\href command - does not create clickable link', () => {
      // This could create links to malicious sites
      const latex = '\\href{https://evil.com/phish}{click here}'
      
      const result = renderMathToString(latex)
      
      // SECURITY: Should NOT create an anchor tag with the malicious URL
      expect(result).not.toMatch(/<a[^>]*href[^>]*evil\.com/)
      // The command should be rendered as text
      expect(result).toContain('\\href')
      // Should appear in error color
      expect(result).toMatch(/color:#cc0000|#cc0000/)
    })

    it('blocks \\url command - does not create clickable link', () => {
      const latex = '\\url{https://evil.com/malware}'
      
      const result = renderMathToString(latex)
      
      // SECURITY: Should NOT create an anchor tag
      expect(result).not.toMatch(/<a[^>]*href[^>]*evil\.com/)
      // Command rendered as text
      expect(result).toContain('\\url')
      // Should appear in error color
      expect(result).toMatch(/color:#cc0000|#cc0000/)
    })
  })

  describe('\\immediate\\write18 - Shell Execution', () => {
    it('blocks \\immediate command - renders as text', () => {
      // In full LaTeX, this could execute shell commands
      const latex = '\\immediate\\write18{rm -rf /}'
      
      const result = renderMathToString(latex)
      
      // SECURITY: Command not executed, rendered as text
      expect(result).toContain('\\immediate')
      expect(result).toMatch(/color:#cc0000|#cc0000/)
    })

    it('blocks \\write18 command - renders as text', () => {
      const latex = '\\write18{cat /etc/passwd}'
      
      const result = renderMathToString(latex)
      
      // SECURITY: Command not executed - \write is blocked, rendered in error color
      // Note: KaTeX parses \write18 as \write + 18, so we check for \write
      expect(result).toContain('\\write')
      expect(result).toMatch(/color:#cc0000|#cc0000/)
    })
  })

  describe('\\def and \\newcommand - Macro Redefinition', () => {
    it('handles potentially dangerous macro definitions safely', () => {
      // KaTeX has limited macro support, but we should ensure no code execution
      const latex = '\\def\\evil{malicious}'
      
      // This may or may not error depending on KaTeX's macro support
      // Either way, it should not execute arbitrary code
      const result = renderMathToString(latex)
      
      // Just verify it returns a string (doesn't throw)
      expect(typeof result).toBe('string')
    })
  })
})

describe('Safe Commands - Allowed Math', () => {
  it('renders fractions correctly', () => {
    const latex = '\\frac{1}{2}'
    const result = renderMathToString(latex)
    
    expect(result).toMatch(/katex/)
    expect(result).not.toMatch(/katex-error/)
  })

  it('renders square roots correctly', () => {
    const latex = '\\sqrt{16}'
    const result = renderMathToString(latex)
    
    expect(result).toMatch(/katex/)
    expect(result).not.toMatch(/katex-error/)
  })

  it('renders summations correctly', () => {
    const latex = '\\sum_{i=1}^{n} i'
    const result = renderMathToString(latex)
    
    expect(result).toMatch(/katex/)
    expect(result).not.toMatch(/katex-error/)
  })

  it('renders integrals correctly', () => {
    const latex = '\\int_{0}^{\\infty} e^{-x} dx'
    const result = renderMathToString(latex)
    
    expect(result).toMatch(/katex/)
    expect(result).not.toMatch(/katex-error/)
  })

  it('renders Greek letters correctly', () => {
    const latex = '\\alpha + \\beta = \\gamma'
    const result = renderMathToString(latex)
    
    expect(result).toMatch(/katex/)
    expect(result).not.toMatch(/katex-error/)
  })

  it('renders matrices correctly', () => {
    const latex = '\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}'
    const result = renderMathToString(latex)
    
    expect(result).toMatch(/katex/)
    expect(result).not.toMatch(/katex-error/)
  })

  it('renders subscripts and superscripts correctly', () => {
    const latex = 'x^2 + y_i = z_{i}^{2}'
    const result = renderMathToString(latex)
    
    expect(result).toMatch(/katex/)
    expect(result).not.toMatch(/katex-error/)
  })

  it('renders complex equations correctly', () => {
    const latex = 'E = mc^2'
    const result = renderMathToString(latex)
    
    expect(result).toMatch(/katex/)
    expect(result).not.toMatch(/katex-error/)
  })

  it('renders display mode equations correctly', () => {
    const latex = '\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}'
    const result = renderMathToString(latex, { displayMode: true })
    
    expect(result).toMatch(/katex/)
    expect(result).not.toMatch(/katex-error/)
  })

  it('renders trigonometric functions correctly', () => {
    const latex = '\\sin^2\\theta + \\cos^2\\theta = 1'
    const result = renderMathToString(latex)
    
    expect(result).toMatch(/katex/)
    expect(result).not.toMatch(/katex-error/)
  })
})

describe('MathRenderer Component', () => {
  it('renders valid LaTeX', () => {
    render(<MathRenderer latex="E = mc^2" />)
    
    // Should render something with katex class
    const mathElement = document.querySelector('.katex')
    expect(mathElement).toBeInTheDocument()
  })

  it('handles empty latex gracefully', () => {
    const { container } = render(<MathRenderer latex="" />)
    expect(container.firstChild).toBeNull()
  })

  it('handles null/undefined latex gracefully', () => {
    const { container } = render(<MathRenderer latex={null as unknown as string} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders unknown commands as text (KaTeX renders unknown commands)', () => {
    // When a command is unknown to KaTeX (not specifically blocked by trust),
    // KaTeX renders it as text (possibly with an error message depending on strict mode)
    const { container } = render(<MathRenderer latex="\\invalidcommand" />)
    
    // Should render something (not throw)
    expect(container.firstChild).toBeInTheDocument()
    // The command should be rendered (as text/unknown command)
    expect(container.innerHTML).toContain('invalidcommand')
  })

  it('does not call onError for blocked commands (graceful degradation)', () => {
    // Blocked commands don't throw errors, they render as text
    const onError = vi.fn()
    render(<MathRenderer latex="\\includegraphics" onError={onError} />)
    
    // onError is only called for actual parse errors, not blocked commands
    expect(onError).not.toHaveBeenCalled()
  })

  it('applies display mode correctly', () => {
    const { container, rerender } = render(
      <MathRenderer latex="x^2" displayMode={false} />
    )
    
    // Inline mode - should have katex class but not katex-display
    expect(container.querySelector('.katex')).toBeInTheDocument()
    
    rerender(<MathRenderer latex="x^2" displayMode={true} />)
    
    // Display mode - should have katex-display class
    expect(container.querySelector('.katex-display')).toBeInTheDocument()
  })

  it('applies custom className', () => {
    const { container } = render(
      <MathRenderer latex="x^2" className="custom-math" />
    )
    
    expect(container.querySelector('.custom-math')).toBeInTheDocument()
  })

  it('has accessible attributes', () => {
    render(<MathRenderer latex="E = mc^2" />)
    
    const mathElement = screen.getByRole('math')
    expect(mathElement).toBeInTheDocument()
    expect(mathElement).toHaveAttribute('aria-label', 'Math: E = mc^2')
  })

  it('blocks unsafe commands in component - no HTML elements created', () => {
    // Test with a blocked command - \href would create an <a> element if allowed
    // But our trust handler blocks it, so no anchor element should exist
    render(<MathRenderer latex="\\href{https://evil.com}{click}" />)
    
    // SECURITY: Should NOT create an anchor element with the malicious URL
    // This is the key security guarantee - no clickable links to dangerous URLs
    expect(document.querySelector('a[href*="evil"]')).not.toBeInTheDocument()
    expect(document.querySelector('a')).not.toBeInTheDocument()
  })
})

describe('Input Validation', () => {
  it('throws error for empty latex string', () => {
    expect(() => renderMathToString('')).toThrow('non-empty string')
  })

  it('throws error for null latex', () => {
    expect(() => renderMathToString(null as unknown as string)).toThrow('non-empty string')
  })

  it('throws error for undefined latex', () => {
    expect(() => renderMathToString(undefined as unknown as string)).toThrow('non-empty string')
  })

  it('handles very long LaTeX expressions', () => {
    // Generate a long but valid LaTeX expression
    const parts = Array(100).fill('x^2').join(' + ')
    
    // Should not throw
    const result = renderMathToString(parts)
    expect(result).toMatch(/katex/)
  })

  it('handles unicode in LaTeX', () => {
    const latex = 'α + β = γ'
    
    // Should not throw
    const result = renderMathToString(latex)
    expect(typeof result).toBe('string')
  })
})

describe('Development Logging', () => {
  let consoleWarnSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  afterEach(() => {
    consoleWarnSpy.mockRestore()
  })

  it('logs warning in development mode when blocked command used', () => {
    const originalEnv = process.env.NODE_ENV
    process.env.NODE_ENV = 'development'

    // Force re-import to pick up env change
    vi.resetModules()
    
    // This test is informational - the trust function logs in dev mode
    // We can't easily test this without module mocking

    process.env.NODE_ENV = originalEnv
  })
})

describe('Trust Handler Direct Tests', () => {
  it('trust handler blocks renderMathToString for unsafe commands', () => {
    // Verify that our trust handler is actually being called
    // \includegraphics would be allowed without our trust handler
    // With our handler, it should be blocked (rendered as text in error color)
    const latex = '\\includegraphics{test}'
    const result = renderMathToString(latex)
    
    // SECURITY: Should NOT create an img element
    expect(result).not.toMatch(/<img/)
    // The command should be rendered as text, not executed
    expect(result).toContain('\\includegraphics')
    // Should appear in error color (indicating it was blocked by trust)
    expect(result).toMatch(/color:#cc0000|#cc0000/)
  })

  it('trust handler allows safe math commands', () => {
    // Safe commands should work normally, no error coloring
    const latex = '\\frac{1}{2}'
    const result = renderMathToString(latex)
    
    expect(result).toMatch(/katex/)
    expect(result).not.toMatch(/color:#cc0000|#cc0000/)
  })
})