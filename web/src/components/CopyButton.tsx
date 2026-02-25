/**
 * CopyButton — shared clipboard copy button.
 *
 * Falls back to execCommand for non-HTTPS contexts (http://0.0.0.0, etc.)
 * where navigator.clipboard is unavailable.
 */

import { useState, useCallback } from 'react'

export interface CopyButtonProps {
  /** Text to copy to clipboard */
  text: string
  /** Accessible label describing what is being copied */
  label: string
  /** Button display text (default: 'copy') */
  buttonText?: string
  /** Optional CSS class override */
  className?: string
}

export function CopyButton({
  text,
  label,
  buttonText = 'copy',
  className = 'copy-btn',
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
      } else {
        // Fallback for non-HTTPS contexts (http://0.0.0.0, http://localhost)
        const el = document.createElement('textarea')
        el.value = text
        el.style.position = 'fixed'
        el.style.opacity = '0'
        document.body.appendChild(el)
        el.select()
        document.execCommand('copy')
        document.body.removeChild(el)
      }
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Copy failed — silently ignore
    }
  }, [text])

  return (
    <button
      className={className}
      onClick={handleCopy}
      aria-label={copied ? 'Copied' : `Copy ${label}`}
      title={copied ? 'Copied!' : `Copy ${label}`}
      type="button"
    >
      {copied ? 'copied' : buttonText}
    </button>
  )
}
