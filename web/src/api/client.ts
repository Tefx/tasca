/**
 * HTTP client configuration for API requests.
 *
 * Supports admin authentication via Bearer token.
 * Token is set externally by AuthContext and never logged.
 */

const API_BASE = '/api/v1'

/** Module-level auth token (set by AuthContext, used by apiClient) */
let authToken: string | null = null
let unauthorizedHandler: (() => void) | null = null

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

/** Registers AuthContext recovery for resource-route 401 responses. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
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

/** Build request headers without exposing the credential value to callers. */
function requestHeaders(options?: RequestInit): Record<string, string> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options?.headers as Record<string, string> | undefined),
  }

  if (authToken) {
    headers.Authorization = `Bearer ${authToken}`
  }

  return headers
}

function responseDetail(body: unknown): string | null {
  if (typeof body !== 'object' || body === null) return null
  if ('detail' in body && typeof body.detail === 'string') return body.detail
  if (
    'error' in body &&
    typeof body.error === 'object' &&
    body.error !== null &&
    'message' in body.error &&
    typeof body.error.message === 'string'
  ) {
    return body.error.message
  }
  return null
}

async function requestError(response: Response, dispatchedToken: string | null): Promise<never> {
  if (response.status === 401) {
    // A delayed Viewer request must not clear a credential installed by elevation.
    if (authToken === dispatchedToken) {
      authToken = null
      unauthorizedHandler?.()
    }
    throw new AuthError('Access credential was not accepted')
  }

  let detail = `${response.status} ${response.statusText}`
  try {
    detail = responseDetail(await response.json()) ?? detail
  } catch {
    // Keep the HTTP status fallback when the error body is not JSON.
  }
  throw new ApiError(`API Error: ${detail}`, response.status)
}

/**
 * Make an authenticated API request and return its raw successful response.
 * Use this for downloads or other non-JSON responses.
 */
export async function apiFetch(path: string, options?: RequestInit): Promise<Response> {
  const dispatchedToken = authToken
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: requestHeaders(options),
  })

  if (!response.ok) {
    return requestError(response, dispatchedToken)
  }

  return response
}

/**
 * HTTP client for JSON API requests.
 *
 * Automatically includes a validated Authorization header when configured and
 * handles resource-route 401 recovery through AuthContext.
 *
 * @example
 * ```typescript
 * const tables = await apiClient<Table[]>('/tables')
 * const newTable = await apiClient<Table>('/tables', {
 *   method: 'POST',
 *   body: JSON.stringify({ question: 'What to discuss?' }),
 * })
 * ```
 */
export async function apiClient<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await apiFetch(path, options)

  if (response.status === 204) {
    return undefined as T
  }

  return response.json() as Promise<T>
}

export type AccessRole = 'viewer' | 'admin'

/**
 * Validate a submitted or restored credential without changing client state.
 * Callers persist it only after this returns a recognized role.
 */
export async function validateAccessCredential(credential: string): Promise<AccessRole> {
  const response = await fetch(`${API_BASE}/auth/validate`, {
    headers: { Authorization: `Bearer ${credential}` },
  })

  if (response.status === 401) {
    throw new AuthError('Credential was not accepted')
  }
  if (!response.ok) {
    throw new ApiError(`Credential validation failed (${response.status})`, response.status)
  }

  const payload: unknown = await response.json()
  if (
    typeof payload !== 'object' ||
    payload === null ||
    !('role' in payload) ||
    (payload.role !== 'viewer' && payload.role !== 'admin')
  ) {
    throw new ApiError('Credential validation returned an invalid role', response.status)
  }

  return payload.role
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