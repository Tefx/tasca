/**
 * AuthConnector - Bridges AuthContext with API client.
 *
 * This component listens to auth state changes and updates the
 * API client's token. It renders nothing but must be placed
 * inside AuthProvider.
 */

import { useEffect } from 'react'
import { useAuth } from '../auth/AuthContext'
import { setAuthToken } from './client'

/**
 * Connects AuthContext to the API client.
 *
 * Listens to auth state changes and updates the API client's token.
 * Renders nothing but must be placed inside AuthProvider.
 *
 * @example
 * ```tsx
 * // Place inside AuthProvider in your app root
 * <AuthProvider>
 *   <AuthConnector />
 *   <App />
 * </AuthProvider>
 * ```
 */
export function AuthConnector(): null {
  const { getToken } = useAuth()

  useEffect(() => {
    // Update API client token whenever auth state changes
    setAuthToken(getToken())
  }, [getToken])

  return null
}