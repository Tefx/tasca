/**
 * Unit tests for CommandConsole component.
 *
 * Covers:
 * - Submit happy path (postSaying called, input cleared)
 * - Submit error path (error shown with role="alert")
 * - Pause transition (pauseTable called, button shows loading state)
 * - Resume transition (resumeTable called)
 *
 * Design source: CommandConsole.tsx — CommandConsoleProps interface,
 * state machine (idle → pausing/resuming → idle), useAuth dependency.
 */

import { describe, it, expect, vi, beforeEach, type Mock } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { CommandConsole } from './CommandConsole'
import type { Table as TableType } from '../api/tables'
import type { Seat } from '../api/sayings'
import type { PatronInfo } from './SeatDeck'

// =============================================================================
// Module Mocks
// =============================================================================

vi.mock('../api/sayings', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/sayings')>()
  return {
    ...actual,
    postSaying: vi.fn(),
  }
})

vi.mock('../api/tables', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/tables')>()
  return {
    ...actual,
    pauseTable: vi.fn(),
    resumeTable: vi.fn(),
  }
})

// Mock useAuth so we control admin/viewer state per test.
// Source: AuthContext.tsx — useAuth returns { mode, hasToken, ... }
vi.mock('../auth/AuthContext', () => ({
  useAuth: vi.fn(),
}))

// =============================================================================
// Import mocked modules AFTER vi.mock declarations
// =============================================================================

import { postSaying } from '../api/sayings'
import { pauseTable, resumeTable } from '../api/tables'
import { useAuth } from '../auth/AuthContext'

// =============================================================================
// Test Fixtures
// =============================================================================

/** Minimal Table fixture with status 'open'. */
function makeTable(overrides: Partial<TableType> = {}): TableType {
  return {
    id: 'table-001',
    question: 'What is the plan?',
    context: null,
    status: 'open',
    version: 1,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    ...overrides,
  }
}

/** A single joined Seat fixture. */
function makeSeat(overrides: Partial<Seat> = {}): Seat {
  return {
    id: 'seat-001',
    table_id: 'table-001',
    patron_id: 'patron-001',
    state: 'joined',
    // Recent heartbeat ensures presence is 'active'
    last_heartbeat: new Date().toISOString(),
    joined_at: '2024-01-01T00:00:00Z',
    ...overrides,
  }
}

/** A PatronInfo fixture for an agent-kind patron. */
function makePatronInfo(overrides: Partial<PatronInfo> = {}): PatronInfo {
  return {
    id: 'patron-001',
    name: 'Agent Alpha',
    kind: 'agent',
    ...overrides,
  }
}

/** Helper: configure useAuth mock for admin mode. */
function asAdmin() {
  (useAuth as Mock).mockReturnValue({
    mode: 'admin',
    hasToken: true,
    setToken: vi.fn(),
    clearToken: vi.fn(),
    enterAdminMode: vi.fn(),
    enterViewerMode: vi.fn(),
    getToken: vi.fn(() => 'test-token'),
  })
}

/** Helper: configure useAuth mock for viewer mode. */
function asViewer() {
  (useAuth as Mock).mockReturnValue({
    mode: 'viewer',
    hasToken: false,
    setToken: vi.fn(),
    clearToken: vi.fn(),
    enterAdminMode: vi.fn(),
    enterViewerMode: vi.fn(),
    getToken: vi.fn(() => null),
  })
}

// =============================================================================
// Tests
// =============================================================================

beforeEach(() => {
  vi.clearAllMocks()
})

describe('CommandConsole — submit', () => {
  it('submit happy path: postSaying is called and input is cleared on success', async () => {
    // Arrange
    asAdmin()
    const mockSaying = {
      id: 'saying-1',
      table_id: 'table-001',
      sequence: 1,
      speaker: { kind: 'human' as const, name: 'Human', patron_id: null },
      content: 'Hello world',
      pinned: false,
      created_at: '2024-01-01T00:00:00Z',
    }
    ;(postSaying as Mock).mockResolvedValue(mockSaying)

    const table = makeTable()
    const seats = [makeSeat()]
    const onPosted = vi.fn()

    render(<CommandConsole table={table} seats={seats} onPosted={onPosted} />)

    // Act: type into the MentionInput textarea
    const textarea = screen.getByRole('textbox', { name: /message input/i })
    fireEvent.change(textarea, { target: { value: 'Hello world' } })

    // Click Send button (text content: "Send", title: "Send saying (Enter)")
    const sendButton = screen.getByRole('button', { name: /^send$/i })
    fireEvent.click(sendButton)

    // Assert
    await waitFor(() => {
      expect(postSaying).toHaveBeenCalledTimes(1)
      expect(postSaying).toHaveBeenCalledWith('table-001', {
        speaker_name: 'Human',
        content: 'Hello world',
        patron_id: null,
      })
    })

    // Input should be cleared after successful post
    await waitFor(() => {
      expect(textarea).toHaveValue('')
    })

    // onPosted callback should have been called
    expect(onPosted).toHaveBeenCalledTimes(1)
  })

  it('submit error path: postSaying rejection shows error with role="alert"', async () => {
    // Arrange
    asAdmin()
    ;(postSaying as Mock).mockRejectedValue(new Error('Network error'))

    const table = makeTable()
    const seats = [makeSeat()]

    render(<CommandConsole table={table} seats={seats} />)

    // Act: type into textarea and click Send
    const textarea = screen.getByRole('textbox', { name: /message input/i })
    fireEvent.change(textarea, { target: { value: 'Hello world' } })

    const sendButton = screen.getByRole('button', { name: /^send$/i })
    fireEvent.click(sendButton)

    // Assert: error message should appear with role="alert"
    await waitFor(() => {
      const alert = screen.getByRole('alert')
      expect(alert).toBeInTheDocument()
      expect(alert).toHaveTextContent('Network error')
    })

    // postSaying was called once
    expect(postSaying).toHaveBeenCalledTimes(1)
  })
})

describe('CommandConsole — pause / resume state machine', () => {
  it('pause transition: pauseTable is called when Pause button is clicked', async () => {
    // Arrange: open table + admin mode
    asAdmin()
    const table = makeTable({ status: 'open' })
    const updatedTable = makeTable({ status: 'paused', version: 2 })
    ;(pauseTable as Mock).mockResolvedValue(updatedTable)

    const onStatusChange = vi.fn()
    const seats: Seat[] = []

    render(<CommandConsole table={table} seats={seats} onStatusChange={onStatusChange} />)

    // Pause button should be visible when status is 'open' (text content: "Pause")
    const pauseButton = screen.getByRole('button', { name: /^pause$/i })
    expect(pauseButton).toBeInTheDocument()

    // Act
    fireEvent.click(pauseButton)

    // During the async call the button shows "Pausing..."
    await waitFor(() => {
      expect(pauseTable).toHaveBeenCalledTimes(1)
      expect(pauseTable).toHaveBeenCalledWith(table)
    })

    // onStatusChange called with updated table
    await waitFor(() => {
      expect(onStatusChange).toHaveBeenCalledWith(updatedTable)
    })
  })

  it('resume transition: resumeTable is called when Resume button is clicked', async () => {
    // Arrange: paused table + admin mode
    asAdmin()
    const table = makeTable({ status: 'paused' })
    const updatedTable = makeTable({ status: 'open', version: 2 })
    ;(resumeTable as Mock).mockResolvedValue(updatedTable)

    const onStatusChange = vi.fn()
    const seats: Seat[] = []

    render(<CommandConsole table={table} seats={seats} onStatusChange={onStatusChange} />)

    // Resume button should be visible when status is 'paused' (text content: "Resume")
    const resumeButton = screen.getByRole('button', { name: /^resume$/i })
    expect(resumeButton).toBeInTheDocument()

    // Act
    fireEvent.click(resumeButton)

    // Assert
    await waitFor(() => {
      expect(resumeTable).toHaveBeenCalledTimes(1)
      expect(resumeTable).toHaveBeenCalledWith(table)
    })

    await waitFor(() => {
      expect(onStatusChange).toHaveBeenCalledWith(updatedTable)
    })
  })

  it('pause loading state: Pause button shows "Pausing..." during async call', async () => {
    // Arrange: block resolution until we can inspect mid-flight state
    asAdmin()
    const table = makeTable({ status: 'open' })

    let resolvePause!: (value: TableType) => void
    const pausePromise = new Promise<TableType>((resolve) => {
      resolvePause = resolve
    })
    ;(pauseTable as Mock).mockReturnValue(pausePromise)

    const seats: Seat[] = []
    render(<CommandConsole table={table} seats={seats} />)

    // Source: CommandConsole.tsx — button text content is "Pause" when idle
    const pauseButton = screen.getByRole('button', { name: /^pause$/i })

    // Act: click but don't await resolution
    act(() => {
      fireEvent.click(pauseButton)
    })

    // Mid-flight: button text should switch to "Pausing..."
    // Source: CommandConsole.tsx — {controlState === 'pausing' ? 'Pausing...' : 'Pause'}
    await waitFor(() => {
      expect(screen.getByText('Pausing...')).toBeInTheDocument()
    })

    // Verify the button is disabled while operating
    // Source: CommandConsole.tsx — disabled={isOperating}
    expect(pauseButton).toBeDisabled()

    // Clean up: resolve the promise so the test does not leak
    await act(async () => {
      resolvePause(makeTable({ status: 'paused', version: 2 }))
      await pausePromise
    })
  })
})

describe('CommandConsole — viewer mode', () => {
  it('does not render send button or controls when in viewer mode', () => {
    // Source: CommandConsole.tsx — isAdmin = mode === 'admin' && hasToken
    asViewer()
    const table = makeTable()
    const seats = [makeSeat()]

    render(<CommandConsole table={table} seats={seats} />)

    // Send button not rendered (text content "Send")
    expect(screen.queryByRole('button', { name: /^send$/i })).not.toBeInTheDocument()

    // Pause/Resume controls not rendered (text content "Pause" / "Resume")
    expect(screen.queryByRole('button', { name: /^pause$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^resume$/i })).not.toBeInTheDocument()
  })
})

describe('RequestSummaryButton — patron filter', () => {
  it('only renders Request Summary button when joined non-offline seats are present', () => {
    // Arrange: agent patron in seats
    asAdmin()
    const table = makeTable()
    const agentPatronId = 'patron-agent-1'
    const agentSeat = makeSeat({ patron_id: agentPatronId, state: 'joined' })
    const agentPatron = makePatronInfo({ id: agentPatronId, kind: 'agent' })
    const patrons = new Map<string, PatronInfo>([[agentPatronId, agentPatron]])

    render(<CommandConsole table={table} seats={[agentSeat]} patrons={patrons} />)

    // Request Summary button should appear (text content: "Request Summary")
    const summaryButton = screen.getByRole('button', { name: /^request summary$/i })
    expect(summaryButton).toBeInTheDocument()
  })

  it('does not render Request Summary button when all seats have left', () => {
    // Arrange: all seats in 'left' state → selectablePatrons = []
    asAdmin()
    const table = makeTable()
    const leftSeat = makeSeat({ state: 'left' })

    render(<CommandConsole table={table} seats={[leftSeat]} />)

    // RequestSummaryButton returns null when no selectable patrons
    expect(screen.queryByRole('button', { name: /^request summary$/i })).not.toBeInTheDocument()
  })
})
