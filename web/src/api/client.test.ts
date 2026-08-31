import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiClient, setAuthToken, setUnauthorizedHandler } from './client'

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText: 'Request failed',
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  setAuthToken(null)
  setUnauthorizedHandler(null)
  vi.unstubAllGlobals()
})

describe('apiClient non-auth errors', () => {
  it('preserves standard and legacy server error details', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ error: { message: 'Table is closed' } }, 409))
      .mockResolvedValueOnce(jsonResponse({ detail: 'Legacy route failure' }, 422))
    vi.stubGlobal('fetch', fetchMock)

    await expect(apiClient('/tables/current')).rejects.toMatchObject({
      name: 'ApiError',
      message: 'API Error: Table is closed',
      status: 409,
    })
    await expect(apiClient('/legacy')).rejects.toMatchObject({
      name: 'ApiError',
      message: 'API Error: Legacy route failure',
      status: 422,
    })
  })
})
