/**
 * Security tests for Markdown rendering.
 *
 * Verifies that raw HTML is NOT rendered in Markdown content.
 * This protects against XSS attacks through user-supplied content.
 *
 * react-markdown v10 is secure by default:
 * - Raw HTML is escaped (not rendered)
 * - rehype-raw is NOT installed (would enable raw HTML)
 *
 * @see https://github.com/remarkjs/react-markdown#security
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

describe('Markdown Security', () => {
  /**
   * Test that raw HTML script tags are escaped, not executed.
   * This is the primary XSS attack vector in Markdown.
   */
  it('escapes <script> tags (XSS prevention)', () => {
    const maliciousMarkdown = `Hello <script>alert('XSS')</script> world`
    
    render(
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {maliciousMarkdown}
      </ReactMarkdown>
    )
    
    // The script tag should NOT be in the DOM as an element
    expect(screen.queryByRole('script')).not.toBeInTheDocument()
    
    // The content should be visible as escaped text
    expect(screen.getByText(/Hello.*world/)).toBeInTheDocument()
    
    // Check that the script tag is escaped (visible as text, not executed)
    const container = screen.getByText(/Hello.*world/).parentElement
    expect(container?.innerHTML).toContain('&lt;script')
    expect(container?.innerHTML).toContain('&lt;/script')
  })
  
  /**
   * Test that inline HTML with event handlers is escaped.
   */
  it('escapes HTML with event handlers (onclick XSS prevention)', () => {
    const maliciousMarkdown = `Click <img src="x" onerror="alert('XSS')"> here`
    
    render(
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {maliciousMarkdown}
      </ReactMarkdown>
    )
    
    // No img element with onerror should exist
    const img = document.querySelector('img[onerror]')
    expect(img).toBeNull()
    
    // The HTML should be escaped
    const container = screen.getByText(/Click.*here/).parentElement
    expect(container?.innerHTML).toContain('&lt;img')
  })
  
  /**
   * Test that HTML links with javascript: protocol are handled safely.
   * react-markdown sanitizes javascript: URLs by setting href to empty string.
   */
  it('handles javascript: protocol in links safely', () => {
    const maliciousMarkdown = `[Click me](javascript:alert('XSS'))`
    
    render(
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {maliciousMarkdown}
      </ReactMarkdown>
    )
    
    // The link exists but the href should be sanitized (empty string, not javascript:)
    const link = document.querySelector('a')
    expect(link).not.toBeNull()
    expect(link?.textContent).toBe('Click me')
    
    const href = link?.getAttribute('href')
    
    // Verify the href is NOT the malicious javascript: URL
    // react-markdown sanitizes javascript: URLs by setting href to empty string
    expect(href).not.toMatch(/^javascript:/i)
    expect(href).toBe('')
  })
  
  /**
   * Test that HTML divs with style are escaped.
   */
  it('escapes HTML elements with style attributes', () => {
    const maliciousMarkdown = `<div style="background:url('javascript:alert(1)')">content</div>`
    
    render(
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {maliciousMarkdown}
      </ReactMarkdown>
    )
    
    // No styled div should exist
    const styledDiv = document.querySelector('div[style]')
    expect(styledDiv).toBeNull()
    
    // The HTML should be escaped
    expect(document.body.innerHTML).toContain('&lt;div')
  })
  
  /**
   * Test that HTML forms with action are escaped.
   */
  it('escapes HTML form elements', () => {
    const maliciousMarkdown = `<form action="https://evil.com/steal"><input type="text"></form>`
    
    render(
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {maliciousMarkdown}
      </ReactMarkdown>
    )
    
    // No form should exist
    const form = document.querySelector('form')
    expect(form).toBeNull()
    
    // The HTML should be escaped
    expect(document.body.innerHTML).toContain('&lt;form')
  })
  
  /**
   * Test that legitimate Markdown still works correctly.
   */
  it('renders legitimate Markdown correctly', () => {
    const validMarkdown = `
# Heading

This is **bold** and *italic* text.

- Item 1
- Item 2

[Link](https://example.com)

\`\`\`javascript
const x = 1;
\`\`\`
`
    
    render(
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {validMarkdown}
      </ReactMarkdown>
    )
    
    // Heading should render
    expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument()
    
    // List should render
    expect(screen.getByRole('list')).toBeInTheDocument()
    
    // Link should render correctly
    const link = screen.getByRole('link', { name: /link/i })
    expect(link).toHaveAttribute('href', 'https://example.com')
  })
  
  /**
   * Test that legitimate inline code works.
   */
  it('renders inline code correctly', () => {
    const markdown = 'This has `inline code` in it.'
    
    render(
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {markdown}
      </ReactMarkdown>
    )
    
    expect(screen.getByText('inline code')).toBeInTheDocument()
  })
  
  /**
   * Test GFM tables work correctly.
   */
  it('renders GFM tables correctly', () => {
    const markdown = `
| Name | Value |
|------|-------|
| A    | 1     |
| B    | 2     |
`
    
    render(
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {markdown}
      </ReactMarkdown>
    )
    
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByText('Name')).toBeInTheDocument()
    expect(screen.getByText('Value')).toBeInTheDocument()
  })
})

describe('Markdown Raw HTML Configuration', () => {
  /**
   * Verify default behavior: raw HTML is NOT rendered without rehype-raw.
   * 
   * Security Note: rehype-raw is NOT installed in this project, which means
   * raw HTML cannot be rendered. If rehype-raw is ever added, this test will
   * need to be updated to verify explicit whitelisting is configured.
   */
  it('default configuration does NOT render raw HTML', () => {
    const htmlContent = '<strong>bold</strong>'
    
    render(<ReactMarkdown>{htmlContent}</ReactMarkdown>)
    
    // The strong tag should NOT be rendered as HTML
    // Instead, it should be visible as literal text (escaped)
    const container = document.body.querySelector('p, div')
    
    // The angle brackets should be escaped in the output
    expect(container?.innerHTML).toMatch(/&lt;strong&gt;/)
    
    // No strong element should exist
    const strongElements = document.querySelectorAll('strong')
    expect(strongElements).toHaveLength(0)
  })
})