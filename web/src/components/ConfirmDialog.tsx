/**
 * ConfirmDialog — A modal dialog for confirming destructive actions.
 *
 * Accessible confirmation dialog with focus trap and keyboard support.
 * Design source: Mission Control UI spec.
 */

import { useEffect, useRef, useCallback, type ReactNode } from 'react'
import '../styles/dialog.css'

// =============================================================================
// Types
// =============================================================================

interface ConfirmDialogProps {
  /** Whether the dialog is open */
  isOpen: boolean
  /** Dialog title */
  title: string
  /** Dialog message */
  message: ReactNode
  /** Text for the confirm button */
  confirmLabel?: string
  /** Text for the cancel button */
  cancelLabel?: string
  /** Variant affecting the confirm button style */
  variant?: 'default' | 'danger'
  /** Called when user confirms */
  onConfirm: () => void
  /** Called when user cancels or closes */
  onCancel: () => void
  /** Whether the action is in progress */
  isLoading?: boolean
}

// =============================================================================
// Component
// =============================================================================

/**
 * ConfirmDialog — A modal dialog for confirming destructive actions.
 *
 * Accessible confirmation dialog with focus trap and keyboard support.
 *
 * @example
 * ```tsx
 * <ConfirmDialog
 *   isOpen={showConfirm}
 *   title="Delete Table"
 *   message="Are you sure you want to delete this table? This action cannot be undone."
 *   variant="danger"
 *   confirmLabel="Delete"
 *   onConfirm={handleDelete}
 *   onCancel={() => setShowConfirm(false)}
 * />
 * ```
 */
export function ConfirmDialog({
  isOpen,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  variant = 'default',
  onConfirm,
  onCancel,
  isLoading = false,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const confirmButtonRef = useRef<HTMLButtonElement>(null)
  const lastActiveElement = useRef<Element | null>(null)

  // Focus management
  useEffect(() => {
    if (isOpen) {
      // Store the last active element
      lastActiveElement.current = document.activeElement
      // Focus the confirm button
      confirmButtonRef.current?.focus()
      // Prevent body scroll
      document.body.style.overflow = 'hidden'
    } else {
      // Restore body scroll
      document.body.style.overflow = ''
      // Restore focus
      if (lastActiveElement.current instanceof HTMLElement) {
        lastActiveElement.current.focus()
      }
    }

    return () => {
      document.body.style.overflow = ''
    }
  }, [isOpen])

  // Handle keyboard events
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') {
        onCancel()
      }
      if (e.key === 'Tab') {
        // Simple focus trap
        const focusable = dialogRef.current?.querySelectorAll(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        )
        if (focusable && focusable.length > 0) {
          const first = focusable[0] as HTMLElement
          const last = focusable[focusable.length - 1] as HTMLElement

          if (e.shiftKey && document.activeElement === first) {
            e.preventDefault()
            last.focus()
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault()
            first.focus()
          }
        }
      }
    },
    [onCancel]
  )

  // Handle backdrop click
  const handleBackdropClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) {
        onCancel()
      }
    },
    [onCancel]
  )

  if (!isOpen) {
    return null
  }

  return (
    <div
      className="dialog-overlay"
      onClick={handleBackdropClick}
      onKeyDown={handleKeyDown}
      role="dialog"
      aria-modal="true"
      aria-labelledby="dialog-title"
      aria-describedby="dialog-message"
    >
      <div className="dialog-content" ref={dialogRef}>
        <h2 id="dialog-title" className="dialog-title">
          {title}
        </h2>
        <div id="dialog-message" className="dialog-message">
          {message}
        </div>
        <div className="dialog-actions">
          <button
            type="button"
            className="dialog-btn dialog-btn--cancel"
            onClick={onCancel}
            disabled={isLoading}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            ref={confirmButtonRef}
            className={`dialog-btn dialog-btn--confirm dialog-btn--${variant}`}
            onClick={onConfirm}
            disabled={isLoading}
          >
            {isLoading ? 'Please wait...' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}