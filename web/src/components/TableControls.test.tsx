import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { TableControls } from './TableControls'
import { setAuthToken } from '../api/client'
import { type Table as TableType } from '../api/tables'

function makeTable(overrides: Partial<TableType> = {}): TableType {
  return {
    id: 'table-001',
    question: 'Export this transcript',
    context: null,
    status: 'open',
    version: 1,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
  }
}

afterEach(() => {
  setAuthToken(null)
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('TableControls download action', () => {
  it('downloads through authenticated fetch instead of a raw export href', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('table export', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const createObjectUrl = vi.fn(() => 'blob:table-export')
    const revokeObjectUrl = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectUrl })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectUrl })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    setAuthToken('validated-test-credential')

    render(<TableControls table={makeTable()} />)
    fireEvent.click(screen.getByRole('button', { name: /^download$/i }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/tables/table-001/export/markdown?download=true',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer validated-test-credential' }),
      })
    )
    expect(screen.queryByRole('link', { name: /^download$/i })).not.toBeInTheDocument()
    expect(click).toHaveBeenCalledOnce()
    expect(createObjectUrl).toHaveBeenCalledOnce()
    expect(revokeObjectUrl).toHaveBeenCalledWith('blob:table-export')
  })
})
