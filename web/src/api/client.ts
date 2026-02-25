/**
 * HTTP client configuration for API requests.
 *
 * Supports admin authentication via Bearer token.
 * Token is set externally by AuthContext and never logged.
 */

const API_BASE = '/api/v1'

/** Module-level auth token (set by AuthContext, used by apiClient) */
let authToken: string | null = null

/**
 * Set the auth token for subsequent API requests.
 * Called by AuthContext when token changes.
 * Token should NEVER be logged here.
 *
 * @example
 * ```typescript
 * // Set token on login
 * setAuthToken('admin-secret-token')
 *
 * // Clear token on logout
 * setAuthToken(null)
 * ```
 */
export function setAuthToken(token: string | null): void {
  authToken = token
}

/**
 * Get the current auth token (for debugging/inspection).
 * Returns 'set' or 'not set' - never returns the actual token.
 *
 * @example
 * ```typescript
 * const status = getAuthTokenStatus()
 * if (status === 'set') {
 *   console.log('Token is configured')
 * }
 * ```
 */
export function getAuthTokenStatus(): 'set' | 'not set' {
  return authToken ? 'set' : 'not set'
}

/**
 * HTTP client for API requests.
 *
 * Automatically includes Authorization header when token is set.
 * Handles common error cases including 401 (auth failure).
 *
 * @example
 * ```typescript
 * // GET request
 * const tables = await apiClient<Table[]>('/tables')
 *
 * // POST request
 * const newTable = await apiClient<Table>('/tables', {
 *   method: 'POST',
 *   body: JSON.stringify({ question: 'What to discuss?' })
 * })
 * ```
 */
export async function apiClient<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options?.headers as Record<string, string> | undefined),
  }

  // Add Authorization header if token is set
  if (authToken) {
    headers['Authorization'] = `Bearer ${authToken}`
  }

  const response = await fetch(`${API_BASE}${path}`, {
    headers,
    ...options,
  })

  if (!response.ok) {
    // Special handling for 401 - auth error
    if (response.status === 401) {
      throw new AuthError('Invalid or missing admin token')
    }

    // Try to get error details from response
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body.detail) {
        detail = typeof body.detail === 'string' 
          ? body.detail 
          : JSON.stringify(body.detail)
      }
    } catch {
      // Ignore JSON parse errors
    }

    throw new ApiError(`API Error: ${detail}`, response.status)
  }

  // Handle 204 No Content
  if (response.status === 204) {
    return undefined as T
  }

  return response.json()
}

/**
 * API Error with status code.
 */
export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message)
    this.name = 'ApiError'
  }
}

/**
 * Authentication error (401).
 * Signals that the admin token is invalid or missing.
 */
export class AuthError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'AuthError'
  }
}