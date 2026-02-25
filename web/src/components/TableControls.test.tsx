import { describe, it, expect, vi, type Mock } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { TableControls } from './TableControls'
import { getExportUrl, type Table as TableType } from '../api/tables'

vi.mock('../auth/AuthContext', () => ({
  useAuth: vi.fn(),
}))

vi.mock('../api/tables', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/tables')>()
  return {
    ...actual,
    closeTable: vi.fn(),
  }
})

import { useAuth } from '../auth/AuthContext'

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

/**
 * Mock useAuth to return admin mode for testing.
 * @example
 * // In a test
 * asAdmin()
 * render(<TableControls table={mockTable} />)
 */
function asAdmin() {
  ;(useAuth as Mock).mockReturnValue({
    mode: 'admin',
    hasToken: true,
    setToken: vi.fn(),
    clearToken: vi.fn(),
    enterAdminMode: vi.fn(),
    enterViewerMode: vi.fn(),
    getToken: vi.fn(() => 'token'),
  })
}

function asViewer() {
  ;(useAuth as Mock).mockReturnValue({
    mode: 'viewer',
    hasToken: false,
    setToken: vi.fn(),
    clearToken: vi.fn(),
    enterAdminMode: vi.fn(),
    enterViewerMode: vi.fn(),
    getToken: vi.fn(() => null),
  })
}

describe('getExportUrl', () => {
  it('returns markdown export endpoint with download=true query', () => {
    expect(getExportUrl('table-001', 'markdown')).toBe(
      '/api/v1/tables/table-001/export/markdown?download=true'
    )
  })
})

describe('TableControls download action', () => {
  it('renders download link for viewer mode', () => {
    asViewer()
    render(<TableControls table={makeTable()}  />)

    const link = screen.getByRole('link', { name: /^download$/i })
    expect(link).toBeInTheDocument()
    expect(link).toHaveAttribute(
      'href',
      '/api/v1/tables/table-001/export/markdown?download=true'
    )
    expect(link).toHaveAttribute('download')
  })

  it('renders download link for admin mode and click is not canceled', () => {
    asAdmin()
    render(<TableControls table={makeTable()}  />)

    const link = screen.getByRole('link', { name: /^download$/i })
    expect(link).toBeInTheDocument()
    expect(fireEvent.click(link)).toBe(true)
  })
})
