/**
 * Validated Web access state.
 *
 * Credentials are held in sessionStorage only after `/auth/validate` accepts
 * them. The legacy `tasca_admin_token` entry is deliberately never read.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { AuthError, type AccessRole, validateAccessCredential } from '../api/client'

export type AuthMode = 'viewer' | 'admin'
export type AccessStatus = 'discovering' | 'credential-required' | 'ready' | 'error'

export const WEB_ACCESS_TOKEN_STORAGE_KEY = 'tasca_web_access_token'
const WEB_ACCESS_ROLE_STORAGE_KEY = 'tasca_web_access_role'

type CredentialResult =
  | { ok: true; role: AccessRole }
  | { ok: false; message: string }

interface AccessState {
  status: AccessStatus
  viewerAuthRequired: boolean | null
  token: string | null
  role: AccessRole
  mode: AuthMode
  error: string | null
}

interface AuthContextValue {
  status: AccessStatus
  viewerAuthRequired: boolean | null
  role: AccessRole
  mode: AuthMode
  hasToken: boolean
  error: string | null
  /** Validate a credential for initial Viewer/Admin access. */
  submitCredential: (credential: string) => Promise<CredentialResult>
  /** Validate an admin credential without replacing a current session on failure. */
  elevateToAdmin: (credential: string) => Promise<CredentialResult>
  /** Compatibility alias for initial credential submission. */
  setToken: (credential: string) => Promise<CredentialResult>
  clearToken: () => void
  enterAdminMode: () => void
  enterViewerMode: () => void
  /** Used only by AuthConnector to configure authenticated API requests. */
  getToken: () => string | null
  retryDiscovery: () => void
  /** Called by apiClient after an authenticated resource request returns 401. */
  handleUnauthorized: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

function readStoredCredential(): string | null {
  try {
    return sessionStorage.getItem(WEB_ACCESS_TOKEN_STORAGE_KEY)
  } catch {
    return null
  }
}

function persistValidatedCredential(token: string, role: AccessRole): void {
  try {
    sessionStorage.setItem(WEB_ACCESS_TOKEN_STORAGE_KEY, token)
    sessionStorage.setItem(WEB_ACCESS_ROLE_STORAGE_KEY, role)
  } catch {
    // A validated credential remains in memory when sessionStorage is unavailable.
  }
}

function clearStoredCredential(): void {
  try {
    sessionStorage.removeItem(WEB_ACCESS_TOKEN_STORAGE_KEY)
    sessionStorage.removeItem(WEB_ACCESS_ROLE_STORAGE_KEY)
  } catch {
    // Storage can be unavailable in restricted browser contexts.
  }
}

function credentialFailureMessage(error: unknown): string {
  if (error instanceof Error && error.name === 'AuthError') {
    return 'Credential was not accepted. Try again.'
  }
  return 'Credential could not be validated. Check the connection and try again.'
}

function initialState(): AccessState {
  return {
    status: 'discovering',
    viewerAuthRequired: null,
    token: null,
    role: 'viewer',
    mode: 'viewer',
    error: null,
  }
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}

interface AuthProviderProps {
  children: ReactNode
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [access, setAccess] = useState<AccessState>(initialState)

  const discoverAccess = useCallback(async () => {
    setAccess((previous) => ({ ...previous, status: 'discovering', error: null }))

    try {
      const response = await fetch('/api/v1/health')
      if (!response.ok) {
        throw new Error('Health request failed')
      }
      const health: unknown = await response.json()
      const viewerAuthRequired =
        typeof health === 'object' && health !== null && 'viewer_auth_required' in health
          ? health.viewer_auth_required === true
          : false
      const storedCredential = readStoredCredential()

      if (storedCredential) {
        try {
          const role = await validateAccessCredential(storedCredential)
          persistValidatedCredential(storedCredential, role)
          setAccess({
            status: 'ready',
            viewerAuthRequired,
            token: storedCredential,
            role,
            mode: role,
            error: null,
          })
          return
        } catch (error) {
          if (error instanceof AuthError) {
            clearStoredCredential()
          } else {
            setAccess({
              status: 'error',
              viewerAuthRequired,
              token: null,
              role: 'viewer',
              mode: 'viewer',
              error: 'Unable to check access. Try again.',
            })
            return
          }
        }
      }

      setAccess({
        status: viewerAuthRequired ? 'credential-required' : 'ready',
        viewerAuthRequired,
        token: null,
        role: 'viewer',
        mode: 'viewer',
        error: null,
      })
    } catch {
      setAccess((previous) => ({
        ...previous,
        status: 'error',
        error: 'Unable to check access. Try again.',
      }))
    }
  }, [])

  useEffect(() => {
    void discoverAccess()
  }, [discoverAccess])

  const submitCredential = useCallback(
    async (candidate: string): Promise<CredentialResult> => {
      const credential = candidate.trim()
      if (!credential) {
        return { ok: false, message: 'Enter a credential.' }
      }

      try {
        const role = await validateAccessCredential(credential)
        persistValidatedCredential(credential, role)
        setAccess((previous) => ({
          status: 'ready',
          viewerAuthRequired: previous.viewerAuthRequired,
          token: credential,
          role,
          mode: role,
          error: null,
        }))
        return { ok: true, role }
      } catch (error) {
        return { ok: false, message: credentialFailureMessage(error) }
      }
    },
    []
  )

  const elevateToAdmin = useCallback(
    async (candidate: string): Promise<CredentialResult> => {
      const credential = candidate.trim()
      if (!credential) {
        return { ok: false, message: 'Enter an admin credential.' }
      }

      try {
        const role = await validateAccessCredential(credential)
        if (role !== 'admin') {
          return { ok: false, message: 'An admin credential is required.' }
        }
        persistValidatedCredential(credential, role)
        setAccess((previous) => ({
          ...previous,
          status: 'ready',
          token: credential,
          role,
          mode: 'admin',
          error: null,
        }))
        return { ok: true, role }
      } catch (error) {
        return { ok: false, message: credentialFailureMessage(error) }
      }
    },
    []
  )

  const clearToken = useCallback(() => {
    clearStoredCredential()
    setAccess((previous) => ({
      status: previous.viewerAuthRequired ? 'credential-required' : 'ready',
      viewerAuthRequired: previous.viewerAuthRequired,
      token: null,
      role: 'viewer',
      mode: 'viewer',
      error: null,
    }))
  }, [])

  const enterAdminMode = useCallback(() => {
    setAccess((previous) =>
      previous.token && previous.role === 'admin'
        ? { ...previous, mode: 'admin' }
        : previous
    )
  }, [])

  const enterViewerMode = useCallback(() => {
    setAccess((previous) => ({ ...previous, mode: 'viewer' }))
  }, [])

  const handleUnauthorized = useCallback(() => {
    clearStoredCredential()
    setAccess((previous) => ({
      status: previous.viewerAuthRequired ? 'credential-required' : 'ready',
      viewerAuthRequired: previous.viewerAuthRequired,
      token: null,
      role: 'viewer',
      mode: 'viewer',
      error: null,
    }))
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      status: access.status,
      viewerAuthRequired: access.viewerAuthRequired,
      role: access.role,
      mode: access.mode,
      hasToken: access.token !== null,
      error: access.error,
      submitCredential,
      elevateToAdmin,
      setToken: submitCredential,
      clearToken,
      enterAdminMode,
      enterViewerMode,
      getToken: () => access.token,
      retryDiscovery: () => {
        void discoverAccess()
      },
      handleUnauthorized,
    }),
    [
      access,
      clearToken,
      discoverAccess,
      elevateToAdmin,
      enterAdminMode,
      enterViewerMode,
      handleUnauthorized,
      submitCredential,
    ]
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
