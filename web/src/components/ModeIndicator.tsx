/**
 * ModeIndicator - Visual indicator for Viewer/Admin mode.
 *
 * Shows current mode and allows switching between modes.
 * For admin mode entry, shows a token input dialog.
 */

import { useState, useCallback, type FormEvent, type ChangeEvent } from 'react'
import { useAuth } from '../auth/AuthContext'
import './ModeIndicator.css'

export function ModeIndicator() {
  const { mode, hasToken, setToken, clearToken, enterAdminMode, enterViewerMode } = useAuth()
  const [showTokenInput, setShowTokenInput] = useState(false)
  const [tokenInput, setTokenInput] = useState('')
  const [error, setError] = useState<string | null>(null)

  const handleSwitchToAdmin = useCallback(() => {
    if (hasToken) {
      // Already have token, just switch mode
      enterAdminMode()
    } else {
      // Need to enter token
      setShowTokenInput(true)
      setError(null)
    }
  }, [hasToken, enterAdminMode])

  const handleSwitchToViewer = useCallback(() => {
    enterViewerMode()
    setShowTokenInput(false)
  }, [enterViewerMode])

  const handleLogout = useCallback(() => {
    clearToken()
    setShowTokenInput(false)
    setTokenInput('')
    setError(null)
  }, [clearToken])

  const handleTokenSubmit = useCallback(
    (e: FormEvent) => {
      e.preventDefault()
      const trimmed = tokenInput.trim()
      if (!trimmed) {
        setError('Token cannot be empty')
        return
      }
      setToken(trimmed)
      setShowTokenInput(false)
      setTokenInput('')
      setError(null)
    },
    [tokenInput, setToken]
  )

  const handleTokenChange = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    setTokenInput(e.target.value)
    setError(null)
  }, [])

  const handleCloseDialog = useCallback(() => {
    setShowTokenInput(false)
    setTokenInput('')
    setError(null)
  }, [])

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
              title="Enter admin mode with token"
            >
              Admin
            </button>
          )}

          {mode === 'admin' && (
            <>
              <button
                type="button"
                className="mode-btn mode-btn--viewer"
                onClick={handleSwitchToViewer}
                aria-label="Switch to viewer mode"
                title="Switch to viewer mode (keep token)"
              >
                Viewer
              </button>
              <button
                type="button"
                className="mode-btn mode-btn--logout"
                onClick={handleLogout}
                aria-label="Clear admin token and logout"
                title="Clear token and logout"
              >
                Logout
              </button>
            </>
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
              if (e.key === 'Escape') handleCloseDialog()
            }}
          >
            <h2 id="token-dialog-title" className="token-dialog-title">
              Enter Admin Token
            </h2>
            <p className="token-dialog-hint">
              Token will be stored in session storage and cleared when you close this tab.
            </p>
            <form onSubmit={handleTokenSubmit} className="token-form">
              <label htmlFor="token-input" className="token-label">
                Admin Token
              </label>
              <input
                id="token-input"
                type="password"
                className="token-input"
                value={tokenInput}
                onChange={handleTokenChange}
                placeholder="Enter your admin token"
                autoFocus
                autoComplete="off"
                aria-describedby={error ? 'token-error' : undefined}
                aria-invalid={!!error}
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
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="token-btn token-btn--submit"
                  disabled={!tokenInput.trim()}
                >
                  Enter Admin Mode
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  )
}