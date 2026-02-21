/**
 * Secure Markdown rendering configuration.
 *
 * SECURITY: This module configures react-markdown with security best practices.
 *
 * **Raw HTML is DISABLED by default.**
 *
 * react-markdown v10 does NOT render raw HTML by default. This is a security
 * feature that prevents XSS attacks from user-supplied content.
 *
 * ## Configuration
 *
 * - `rehype-raw` is NOT installed — raw HTML is escaped, not rendered
 * - Links open in new tab with `noopener noreferrer` (in Stream.tsx)
 * - `javascript:` URLs are sanitized (react-markdown default)
 *
 * ## Security Tests
 *
 * See `markdown.security.test.tsx` for verification that:
 * - `<script>` tags are escaped
 * - Event handlers (onclick, onerror) are escaped
 * - `javascript:` protocol is sanitized
 * - HTML forms are escaped
 * - Malicious CSS is escaped
 *
 * @see https://github.com/remarkjs/react-markdown#security
 */

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * Props for the MarkdownRenderer component.
 */
export interface MarkdownRendererProps {
  /** Markdown content to render. */
  children: string
  /** Optional CSS class name for the container. */
  className?: string
}

/**
 * Secure markdown renderer component.
 *
 * Uses react-markdown with GFM (GitHub Flavored Markdown) support.
 * Raw HTML is NOT rendered for security.
 *
 * @example
 * ```tsx
 * <MarkdownRenderer>
 *   # Hello **world**
 *   - Item 1
 *   - Item 2
 * </MarkdownRenderer>
 * ```
 */
export function MarkdownRenderer({ children, className }: MarkdownRendererProps) {
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // SECURITY: rehypePlugins is NOT set, so raw HTML is escaped.
        // Do NOT add rehype-raw without explicit security review.
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}

/**
 * Re-export react-markdown for custom configurations.
 *
 * ⚠️ SECURITY WARNING: If you need custom components or plugins,
 * ensure you do NOT enable raw HTML (rehype-raw) without sanitization.
 */
export { ReactMarkdown, remarkGfm }

/**
 * Default export for convenience.
 */
export default MarkdownRenderer