import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import App from './App'
import { apiClient, setAuthToken, type AccessRole } from './api/client'
import { WEB_ACCESS_TOKEN_STORAGE_KEY } from './auth/AuthContext'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function queueFetch(...responses: Response[]) {
  const fetchMock = vi.fn()
  for (const response of responses) {
    fetchMock.mockResolvedValueOnce(response)
  }
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function authorizationHeader(call: unknown[]): string | null {
  const init = call[1] as RequestInit | undefined
  return new Headers(init?.headers).get('Authorization')
}

async function enterCredential(credential: string) {
  fireEvent.change(screen.getByLabelText('Access credential'), {
    target: { value: credential },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
}

async function renderViewerSession(fetchMock: ReturnType<typeof queueFetch>) {
  render(<App />)
  await screen.findByRole('heading', { name: 'Access required' })
  await enterCredential('viewer-test-credential')
  await screen.findByRole('heading', { name: 'Taproom' })
  return fetchMock
}

beforeEach(() => {
  sessionStorage.clear()
  setAuthToken(null)
  window.history.replaceState({}, '', '/')
})

afterEach(() => {
  setAuthToken(null)
  sessionStorage.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('Web credential gate', () => {
  it('shows a credential gate before configured discussion data and rejects invalid credentials without storage writes', async () => {
    const fetchMock = queueFetch(
      jsonResponse({ viewer_auth_required: true }),
      jsonResponse({ detail: 'Permission denied' }, 401)
    )
    const setItem = vi.spyOn(Storage.prototype, 'setItem')

    render(<App />)

    await screen.findByRole('heading', { name: 'Access required' })
    expect(screen.queryByRole('heading', { name: 'Taproom' })).not.toBeInTheDocument()
    await enterCredential('invalid-test-credential')

    expect(await screen.findByRole('alert')).toHaveTextContent('Credential was not accepted')
    expect(setItem).not.toHaveBeenCalled()
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(authorizationHeader(fetchMock.mock.calls[1])).toBe('Bearer invalid-test-credential')
  })

  it('preserves the direct public Viewer experience when viewer authentication is disabled', async () => {
    const fetchMock = queueFetch(
      jsonResponse({ viewer_auth_required: false }),
      jsonResponse([])
    )

    render(<App />)

    await screen.findByRole('heading', { name: 'Taproom' })
    expect(screen.getByText('👁️ Viewer')).toBeInTheDocument()
    expect(authorizationHeader(fetchMock.mock.calls[1])).toBeNull()
  })

  it('validates a Viewer credential before persistence and sends it on baseline reads', async () => {
    const fetchMock = queueFetch(
      jsonResponse({ viewer_auth_required: true }),
      jsonResponse({ role: 'viewer' satisfies AccessRole }),
      jsonResponse([])
    )

    await renderViewerSession(fetchMock)

    expect(sessionStorage.getItem(WEB_ACCESS_TOKEN_STORAGE_KEY)).toBe('viewer-test-credential')
    expect(authorizationHeader(fetchMock.mock.calls[2])).toBe('Bearer viewer-test-credential')
    expect(screen.queryByDisplayValue('viewer-test-credential')).not.toBeInTheDocument()
  })

  it('revalidates the new session key on reload and ignores the legacy token key', async () => {
    sessionStorage.setItem(WEB_ACCESS_TOKEN_STORAGE_KEY, 'restored-test-credential')
    sessionStorage.setItem('tasca_admin_token', 'legacy-test-credential')
    const fetchMock = queueFetch(
      jsonResponse({ viewer_auth_required: true }),
      jsonResponse({ role: 'viewer' satisfies AccessRole }),
      jsonResponse([])
    )

    render(<App />)

    await screen.findByRole('heading', { name: 'Taproom' })
    expect(authorizationHeader(fetchMock.mock.calls[1])).toBe('Bearer restored-test-credential')
    expect(authorizationHeader(fetchMock.mock.calls[2])).toBe('Bearer restored-test-credential')
    expect(sessionStorage.getItem('tasca_admin_token')).toBe('legacy-test-credential')
  })

  it('preserves a stored credential after transient reload validation failure and retries it before loading data', async () => {
    sessionStorage.setItem(WEB_ACCESS_TOKEN_STORAGE_KEY, 'restored-test-credential')
    const fetchMock = queueFetch(
      jsonResponse({ viewer_auth_required: true }),
      jsonResponse({ error: { message: 'Service unavailable' } }, 503),
      jsonResponse({ viewer_auth_required: true }),
      jsonResponse({ role: 'viewer' satisfies AccessRole }),
      jsonResponse([])
    )

    render(<App />)

    await screen.findByRole('heading', { name: 'Unable to check access' })
    expect(sessionStorage.getItem(WEB_ACCESS_TOKEN_STORAGE_KEY)).toBe('restored-test-credential')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    await screen.findByRole('heading', { name: 'Taproom' })
    expect(authorizationHeader(fetchMock.mock.calls[1])).toBe('Bearer restored-test-credential')
    expect(authorizationHeader(fetchMock.mock.calls[3])).toBe('Bearer restored-test-credential')
    expect(authorizationHeader(fetchMock.mock.calls[4])).toBe('Bearer restored-test-credential')
  })

  it('preserves Viewer access after failed elevation, replaces it after admin validation, and retains its header in Viewer UI mode', async () => {
    const fetchMock = queueFetch(
      jsonResponse({ viewer_auth_required: true }),
      jsonResponse({ role: 'viewer' satisfies AccessRole }),
      jsonResponse([]),
      jsonResponse({ detail: 'Permission denied' }, 401),
      jsonResponse({ role: 'admin' satisfies AccessRole }),
      jsonResponse([]),
      jsonResponse([])
    )

    await renderViewerSession(fetchMock)
    fireEvent.click(screen.getByRole('button', { name: 'Switch to admin mode' }))
    fireEvent.change(screen.getByLabelText('Admin credential'), {
      target: { value: 'rejected-admin-credential' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Enter Admin Mode' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Credential was not accepted')
    expect(sessionStorage.getItem(WEB_ACCESS_TOKEN_STORAGE_KEY)).toBe('viewer-test-credential')
    expect(screen.getByText('👁️ Viewer')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Admin credential'), {
      target: { value: 'admin-test-credential' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Enter Admin Mode' }))

    await screen.findByText('🔐 Admin')
    expect(sessionStorage.getItem(WEB_ACCESS_TOKEN_STORAGE_KEY)).toBe('admin-test-credential')
    await apiClient('/search?q=table')
    expect(authorizationHeader(fetchMock.mock.calls[5])).toBe('Bearer admin-test-credential')

    fireEvent.click(screen.getByRole('button', { name: 'Switch to viewer mode' }))
    await apiClient('/search?q=table')
    expect(authorizationHeader(fetchMock.mock.calls[6])).toBe('Bearer admin-test-credential')
  })

  it('keeps the pending elevation dialog and Viewer credential until admin validation completes', async () => {
    let resolveAdminValidation: ((response: Response) => void) | undefined
    const pendingAdminValidation = new Promise<Response>((resolve) => {
      resolveAdminValidation = resolve
    })
    const fetchMock = queueFetch(
      jsonResponse({ viewer_auth_required: true }),
      jsonResponse({ role: 'viewer' satisfies AccessRole }),
      jsonResponse([]),
      pendingAdminValidation as unknown as Response
    )

    await renderViewerSession(fetchMock)
    fireEvent.click(screen.getByRole('button', { name: 'Switch to admin mode' }))
    fireEvent.change(screen.getByLabelText('Admin credential'), {
      target: { value: 'admin-test-credential' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Enter Admin Mode' }))

    const dialog = screen.getByRole('dialog')
    expect(screen.getByRole('button', { name: 'Checking…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Clear credential and logout' })).toBeDisabled()
    fireEvent.click(dialog)
    fireEvent.keyDown(screen.getByLabelText('Admin credential'), { key: 'Escape' })
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(sessionStorage.getItem(WEB_ACCESS_TOKEN_STORAGE_KEY)).toBe('viewer-test-credential')

    await act(async () => {
      resolveAdminValidation?.(jsonResponse({ role: 'admin' satisfies AccessRole }))
    })

    await screen.findByText('🔐 Admin')
    expect(sessionStorage.getItem(WEB_ACCESS_TOKEN_STORAGE_KEY)).toBe('admin-test-credential')
  })

  it('keeps a newly elevated Admin credential when a delayed Viewer request returns 401', async () => {
    let resolveStaleResponse: ((response: Response) => void) | undefined
    const staleResponse = new Promise<Response>((resolve) => {
      resolveStaleResponse = resolve
    })
    const fetchMock = queueFetch(
      jsonResponse({ viewer_auth_required: true }),
      jsonResponse({ role: 'viewer' satisfies AccessRole }),
      jsonResponse([]),
      // A baseline Viewer request starts before the user elevates.
      staleResponse as unknown as Response,
      jsonResponse({ role: 'admin' satisfies AccessRole }),
      jsonResponse([])
    )

    await renderViewerSession(fetchMock)
    const staleRequest = apiClient('/search?q=table')
    fireEvent.click(screen.getByRole('button', { name: 'Switch to admin mode' }))
    fireEvent.change(screen.getByLabelText('Admin credential'), {
      target: { value: 'admin-test-credential' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Enter Admin Mode' }))
    await screen.findByText('🔐 Admin')

    await act(async () => {
      resolveStaleResponse?.(jsonResponse({ detail: 'Permission denied' }, 401))
      await expect(staleRequest).rejects.toThrow('Access credential was not accepted')
    })

    expect(screen.getByText('🔐 Admin')).toBeInTheDocument()
    expect(sessionStorage.getItem(WEB_ACCESS_TOKEN_STORAGE_KEY)).toBe('admin-test-credential')
    await apiClient('/search?q=table')
    expect(authorizationHeader(fetchMock.mock.calls[5])).toBe('Bearer admin-test-credential')
  })

  it('clears a validated credential and returns configured access to the gate', async () => {
    const fetchMock = queueFetch(
      jsonResponse({ viewer_auth_required: true }),
      jsonResponse({ role: 'viewer' satisfies AccessRole }),
      jsonResponse([])
    )

    await renderViewerSession(fetchMock)
    fireEvent.click(screen.getByRole('button', { name: 'Clear credential and logout' }))

    await screen.findByRole('heading', { name: 'Access required' })
    expect(sessionStorage.getItem(WEB_ACCESS_TOKEN_STORAGE_KEY)).toBeNull()
  })

  it('clears authenticated state and recovers to the gate after a resource-route 401', async () => {
    const fetchMock = queueFetch(
      jsonResponse({ viewer_auth_required: true }),
      jsonResponse({ role: 'viewer' satisfies AccessRole }),
      jsonResponse([]),
      jsonResponse({ detail: 'Permission denied' }, 401)
    )

    await renderViewerSession(fetchMock)
    await act(async () => {
      await expect(apiClient('/search?q=table')).rejects.toThrow('Access credential was not accepted')
    })

    await screen.findByRole('heading', { name: 'Access required' })
    expect(authorizationHeader(fetchMock.mock.calls[3])).toBe('Bearer viewer-test-credential')
    expect(sessionStorage.getItem(WEB_ACCESS_TOKEN_STORAGE_KEY)).toBeNull()
  })
})
