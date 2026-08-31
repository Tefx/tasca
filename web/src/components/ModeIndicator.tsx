import { useState, useCallback, type FormEvent, type ChangeEvent } from 'react'
import { useAuth } from '../auth/AuthContext'
import './ModeIndicator.css'

/**
 * ModeIndicator - Visual indicator for Viewer/Admin mode.
 *
 * Shows current mode and allows switching between modes.
 * For admin mode entry, shows a token input dialog.
 *
 * @example
 * ```tsx
 * // Place in header or toolbar
 * <ModeIndicator />
 * ```
 */
export function ModeIndicator() {
  const { mode, role, hasToken, clearToken, enterAdminMode, enterViewerMode, elevateToAdmin } = useAuth()
  const [showTokenInput, setShowTokenInput] = useState(false)
  const [tokenInput, setTokenInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSwitchToAdmin = useCallback(() => {
    if (role === 'admin' && hasToken) {
      enterAdminMode()
      return
    }
    setShowTokenInput(true)
    setError(null)
  }, [hasToken, enterAdminMode, role])

  const handleSwitchToViewer = useCallback(() => {
    if (isSubmitting) return
    enterViewerMode()
    setShowTokenInput(false)
  }, [enterViewerMode, isSubmitting])

  const handleLogout = useCallback(() => {
    if (isSubmitting) return
    clearToken()
    setShowTokenInput(false)
    setTokenInput('')
    setError(null)
  }, [clearToken, isSubmitting])

  const handleTokenSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault()
      if (isSubmitting) return
      const trimmed = tokenInput.trim()
      if (!trimmed) {
        setError('Enter an admin credential.')
        return
      }
      setIsSubmitting(true)
      setError(null)
      const result = await elevateToAdmin(trimmed)
      if (!result.ok) {
        setError(result.message)
      } else {
        setShowTokenInput(false)
        setTokenInput('')
      }
      setIsSubmitting(false)
    },
    [elevateToAdmin, isSubmitting, tokenInput]
  )

  const handleTokenChange = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    setTokenInput(e.target.value)
    setError(null)
  }, [])

  const handleCloseDialog = useCallback(() => {
    if (isSubmitting) return
    setShowTokenInput(false)
    setTokenInput('')
    setError(null)
  }, [isSubmitting])

  return (
    <>
      <div className="mode-indicator" role="status" aria-live="polite">
        <span className={`mode-badge mode-badge--${mode}`}>
          {mode === 'admin' ? '🔐 Admin' : '👁️ Viewer'}
        </span>

        <div className="mode-actions">
          {mode === 'viewer' && (
            <button
              type="button"
              className="mode-btn mode-btn--enter-admin"
              onClick={handleSwitchToAdmin}
              aria-label="Switch to admin mode"
              title="Validate an admin credential"
            >
              Admin
            </button>
          )}

          {mode === 'admin' && (
            <button
              type="button"
              className="mode-btn mode-btn--viewer"
              onClick={handleSwitchToViewer}
              aria-label="Switch to viewer mode"
              title="Switch to viewer mode (keep credential)"
              disabled={isSubmitting}
            >
              Viewer
            </button>
          )}
          {hasToken && (
            <button
              type="button"
              className="mode-btn mode-btn--logout"
              onClick={handleLogout}
              aria-label="Clear credential and logout"
              title="Clear credential and logout"
              disabled={isSubmitting}
            >
              Logout
            </button>
          )}
        </div>
      </div>

      {/* Token input dialog */}
      {showTokenInput && (
        <div
          className="token-dialog-overlay"
          onClick={handleCloseDialog}
          role="dialog"
          aria-modal="true"
          aria-labelledby="token-dialog-title"
        >
          <div
            className="token-dialog"
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === 'Escape' && !isSubmitting) handleCloseDialog()
            }}
          >
            <h2 id="token-dialog-title" className="token-dialog-title">
              Enter Admin Credential
            </h2>
            <p className="token-dialog-hint">
              The credential is validated before Admin controls are enabled.
            </p>
            <form onSubmit={handleTokenSubmit} className="token-form">
              <label htmlFor="token-input" className="token-label">
                Admin credential
              </label>
              <input
                id="token-input"
                type="password"
                className="token-input"
                value={tokenInput}
                onChange={handleTokenChange}
                placeholder="Enter your admin credential"
                autoFocus
                autoComplete="off"
                aria-describedby={error ? 'token-error' : undefined}
                aria-invalid={!!error}
                disabled={isSubmitting}
              />
              {error && (
                <p id="token-error" className="token-error" role="alert">
                  {error}
                </p>
              )}
              <div className="token-actions">
                <button
                  type="button"
                  className="token-btn token-btn--cancel"
                  onClick={handleCloseDialog}
                  disabled={isSubmitting}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="token-btn token-btn--submit"
                  disabled={!tokenInput.trim() || isSubmitting}
                >
                  {isSubmitting ? 'Checking…' : 'Enter Admin Mode'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  )
}