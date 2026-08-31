/** Bridges validated AuthContext state with the shared API client. */

import { useLayoutEffect } from 'react'
import { useAuth } from '../auth/AuthContext'
import { setAuthToken, setUnauthorizedHandler } from './client'

/**
 * Configures request credentials before route effects can load discussion data.
 * It also turns later resource-route 401 responses into AuthContext recovery.
 */
export function AuthConnector(): null {
  const { getToken, handleUnauthorized } = useAuth()

  useLayoutEffect(() => {
    setAuthToken(getToken())
    setUnauthorizedHandler(handleUnauthorized)

    return () => {
      setAuthToken(null)
      setUnauthorizedHandler(null)
    }
  }, [getToken, handleUnauthorized])

  return null
}
