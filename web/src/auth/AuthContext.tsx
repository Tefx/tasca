/**
 * Authentication context for admin mode.
 *
 * Provides Viewer/Admin mode switching with token stored in sessionStorage
 * (NOT localStorage for security reasons).
 *
 * Token is never logged or displayed in plain text.
 */

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  type ReactNode,
} from 'react'

// =============================================================================
// Types
// =============================================================================

export type AuthMode = 'viewer' | 'admin'

interface AuthContextValue {
  /** Current authentication mode */
  mode: AuthMode
  /** Whether admin token is set (even if currently in viewer mode) */
  hasToken: boolean
  /** Set admin token and switch to admin mode */
  setToken: (token: string) => void
  /** Clear token and switch to viewer mode */
  clearToken: () => void
  /** Switch to admin mode (requires token to be set) */
  enterAdminMode: () => void
  /** Switch to viewer mode (keeps token for quick switch back) */
  enterViewerMode: () => void
  /** Get the current token (for API requests) - returns null in viewer mode or if not set */
  getToken: () => string | null
}

// =============================================================================
// Constants
// =============================================================================

const SESSION_STORAGE_KEY = 'tasca_admin_token'

// =============================================================================
// Context
// =============================================================================

const AuthContext = createContext<AuthContextValue | null>(null)

// =============================================================================
// Hook
// =============================================================================

/**
 * Access the auth context.
 * Must be used within an AuthProvider.
 *
 * @example
 * ```tsx
 * function MyComponent() {
 *   const { mode, hasToken, setToken, clearToken } = useAuth()
 *
 *   if (mode === 'admin') {
 *     return <button onClick={clearToken}>Logout</button>
 *   }
 *   return <button onClick={() => setToken('my-token')}>Login</button>
 * }
 * ```
 */
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}

// =============================================================================
// Provider
// =============================================================================

interface AuthProviderProps {
  children: ReactNode
}

/**
 * Auth provider that manages admin token in sessionStorage.
 *
 * Security decisions:
 * - sessionStorage: Cleared when tab/window closes (not localStorage)
 * - Token never logged or exposed in UI
 * - Mode persisted separately so user can switch easily
 *
 * @example
 * ```tsx
 * // Wrap your app with AuthProvider
 * <AuthProvider>
 *   <AuthConnector />
 *   <App />
 * </AuthProvider>
 * ```
 */
export function AuthProvider({ children }: AuthProviderProps) {
  // Initialize from sessionStorage on mount
  const [token, setTokenState] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem(SESSION_STORAGE_KEY)
    } catch {
      // sessionStorage may be unavailable in private mode
      return null
    }
  })

  const [mode, setMode] = useState<AuthMode>(() => {
    // Default to viewer mode, even if token exists
    return 'viewer'
  })

  // Derive hasToken
  const hasToken = token !== null

  // Set token and switch to admin mode
  const setToken = useCallback((newToken: string) => {
    const trimmed = newToken.trim()
    if (!trimmed) {
      console.warn('AuthContext: Attempted to set empty token')
      return
    }
    try {
      sessionStorage.setItem(SESSION_STORAGE_KEY, trimmed)
    } catch {
      console.warn('AuthContext: sessionStorage unavailable')
    }
    setTokenState(trimmed)
    setMode('admin')
  }, [])

  // Clear token and switch to viewer mode
  const clearToken = useCallback(() => {
    try {
      sessionStorage.removeItem(SESSION_STORAGE_KEY)
    } catch {
      // Ignore if sessionStorage is unavailable
    }
    setTokenState(null)
    setMode('viewer')
  }, [])

  // Switch to admin mode (requires token)
  const enterAdminMode = useCallback(() => {
    if (!token) {
      console.warn('AuthContext: Cannot enter admin mode without token')
      return
    }
    setMode('admin')
  }, [token])

  // Switch to viewer mode (keeps token)
  const enterViewerMode = useCallback(() => {
    setMode('viewer')
  }, [])

  // Get current token (for API) - null if in viewer mode or no token
  const getToken = useCallback(() => {
    if (mode !== 'admin') {
      return null
    }
    return token
  }, [mode, token])

  // Sync token state across tabs (storage event)
  useEffect(() => {
    const handleStorage = (e: StorageEvent) => {
      if (e.key === SESSION_STORAGE_KEY) {
        // sessionStorage changes don't trigger across tabs, but handle anyway
        if (e.newValue === null) {
          setTokenState(null)
          setMode('viewer')
        } else if (e.newValue) {
          setTokenState(e.newValue)
        }
      }
    }

    window.addEventListener('storage', handleStorage)
    return () => window.removeEventListener('storage', handleStorage)
  }, [])

  const value: AuthContextValue = {
    mode,
    hasToken,
    setToken,
    clearToken,
    enterAdminMode,
    enterViewerMode,
    getToken,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}