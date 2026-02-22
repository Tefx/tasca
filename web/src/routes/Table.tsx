/**
 * Table — Mission Control page.
 *
 * Displays a single discussion table with three-column layout:
 * Board (context rail) | Stream (sayings) | SeatDeck (presence).
 *
 * Data fetching:
 *   - Table metadata + seats: fetched once on mount.
 *   - Sayings: live via useSayingsStream long-poll (web.long_poll_stream step).
 *
 * Design source: docs/tasca-web-uiux-v0.1.md (Table View / Mission Control spec)
 */

import { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getTable, type Table as TableType } from '../api/tables'
import { listSeats, type Seat } from '../api/sayings'
import { useSayingsStream } from '../hooks/useLongPoll'
import { Board } from '../components/Board'
import { Stream } from '../components/Stream'
import { SeatDeck, type PatronInfo } from '../components/SeatDeck'
import { ModeIndicator } from '../components/ModeIndicator'
import { TableControls } from '../components/TableControls'
import { CommandConsole } from '../components/CommandConsole'
import '../styles/table.css'

// =============================================================================
// Types
// =============================================================================

interface StaticTableData {
  table: TableType
  seats: Seat[]
  activeCount: number
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'loaded'; data: StaticTableData }

// =============================================================================
// Hooks
// =============================================================================

/**
 * Fetch the static parts of the table page: table metadata and seats.
 *
 * Sayings are intentionally excluded — they are handled by useSayingsStream
 * which subscribes to the long-poll endpoint for real-time updates.
 */
function useStaticTableData(tableId: string | undefined) {
  const [state, setState] = useState<LoadState>({ status: 'loading' })

  const fetchAll = useCallback(async () => {
    if (!tableId) {
      setState({ status: 'error', message: 'No table ID provided' })
      return
    }

    setState({ status: 'loading' })

    try {
      const [table, seatsResponse] = await Promise.all([
        getTable(tableId),
        listSeats(tableId),
      ])

      setState({
        status: 'loaded',
        data: {
          table,
          seats: seatsResponse.seats,
          activeCount: seatsResponse.active_count,
        },
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load table'
      setState({ status: 'error', message })
    }
  }, [tableId])

  useEffect(() => {
    fetchAll()
  }, [fetchAll])

  return { state, refetch: fetchAll }
}

// =============================================================================
// Sub-Components
// =============================================================================

interface CopyButtonProps {
  text: string
  label: string
}

function CopyButton({ text, label }: CopyButtonProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard API may fail in non-HTTPS contexts; acceptable for v0.1
    }
  }, [text])

  return (
    <button
      className="mc-copy-btn"
      onClick={handleCopy}
      aria-label={copied ? 'Copied' : `Copy ${label}`}
      title={copied ? 'Copied!' : `Copy ${label}`}
      type="button"
    >
      {copied ? 'done' : 'copy'}
    </button>
  )
}

/** Get the first 8 characters of a table ID as a placeholder invite code. */
function shortCode(id: string): string {
  return id.slice(0, 8)
}

/** Build the share URL for a table. */
function shareUrl(tableId: string): string {
  return `${window.location.origin}/tables/${tableId}`
}

// =============================================================================
// State Components
// =============================================================================

function LoadingState() {
  return (
    <div className="mc-state" role="status" aria-label="Loading table">
      <div className="mc-spinner" />
      <p>Loading table...</p>
    </div>
  )
}

interface ErrorStateProps {
  message: string
  onRetry: () => void
}

function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div className="mc-state mc-state--error" role="alert">
      <p className="mc-state-title">Failed to load table</p>
      <p className="mc-state-detail">{message}</p>
      <button type="button" className="mc-retry-btn" onClick={onRetry}>
        Retry
      </button>
      <Link to="/" className="mc-hud-back" style={{ marginTop: '0.5rem' }}>
        Back to Watchtower
      </Link>
    </div>
  )
}

// =============================================================================
// HUD Header
// =============================================================================

interface HudProps {
  table: TableType
  onStatusChange: (table: TableType) => void
}

function Hud({ table, onStatusChange }: HudProps) {
  return (
    <header className="mc-hud">
      <Link to="/" className="mc-hud-back" aria-label="Back to Watchtower">
        &larr; Watchtower
      </Link>
      <h1 className="mc-hud-title">{table.question}</h1>
      <TableControls table={table} onStatusChange={onStatusChange} />
      <div className="mc-hud-actions">
        <span className="mc-hud-action">
          <code>{shortCode(table.id)}</code>
          <CopyButton text={shortCode(table.id)} label="invite code" />
        </span>
        <span className="mc-hud-action">
          <CopyButton text={shareUrl(table.id)} label="share URL" />
        </span>
      </div>
      <ModeIndicator />
    </header>
  )
}

// =============================================================================
// Main Component
// =============================================================================

export function Table() {
  const { tableId } = useParams<{ tableId: string }>()

  // Static data: table metadata + seats (one-time fetch).
  const { state, refetch } = useStaticTableData(tableId)

  // Local table state for status updates (merged with stream updates).
  const [localTable, setLocalTable] = useState<TableType | null>(null)

  // Live data: sayings stream via long-poll.
  const {
    sayings,
    table: streamTable,
    connectionStatus,
  } = useSayingsStream(tableId)

  // ---------------------------------------------------------------------------
  // Handle status changes from TableControls
  // ---------------------------------------------------------------------------

  const handleStatusChange = useCallback((updatedTable: TableType) => {
    setLocalTable(updatedTable)
  }, [])

  // ---------------------------------------------------------------------------
  // Handle errors from console controls
  // ---------------------------------------------------------------------------

  const handleError = useCallback((error: Error) => {
    // For now, just log the error. Could be enhanced with toast notifications.
    console.error('Table control error:', error.message)
  }, [])

  // ---------------------------------------------------------------------------
  // Reset local table when static data loads
  // ---------------------------------------------------------------------------

  useEffect(() => {
    if (state.status === 'loaded') {
      setLocalTable(state.data.table)
    }
  }, [state])

  // ---------------------------------------------------------------------------
  // Build patrons map from sayings for mention autocomplete
  // ---------------------------------------------------------------------------

  const patronsMap = useMemo<Map<string, PatronInfo>>(() => {
    const map = new Map<string, PatronInfo>()
    // Extract unique patrons from saying speakers
    for (const saying of sayings) {
      const { speaker } = saying
      // Only add if patron_id exists and not already in map
      if (speaker.patron_id && !map.has(speaker.patron_id)) {
        // Map 'patron' speaker kind to 'agent' patron kind (they're equivalent)
        const patronKind = speaker.kind === 'patron' ? 'agent' : speaker.kind
        map.set(speaker.patron_id, {
          id: speaker.patron_id,
          name: speaker.name,
          kind: patronKind,
        })
      }
    }
    return map
  }, [sayings])

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (state.status === 'loading') {
    return (
      <div className="mc">
        <LoadingState />
      </div>
    )
  }

  if (state.status === 'error') {
    return (
      <div className="mc">
        <ErrorState message={state.message} onRetry={refetch} />
      </div>
    )
  }

  const { seats, activeCount } = state.data

  // Table priority: local state (from controls) > stream (live updates) > static
  // This ensures status changes from controls take precedence.
  const table = localTable ?? streamTable ?? state.data.table

  return (
    <div className="mc">
      <Hud table={table} onStatusChange={handleStatusChange} />
      <div className="mc-columns">
        <div className="mc-col-left">
          <Board table={table} />
        </div>
        <div className="mc-col-center">
          <Stream sayings={sayings} connectionStatus={connectionStatus} />
          <CommandConsole 
            table={table} 
            seats={seats} 
            patrons={patronsMap}
            onStatusChange={handleStatusChange}
            onError={handleError}
          />
        </div>
        <SeatDeck seats={seats} activeCount={activeCount} patrons={patronsMap} />
      </div>
    </div>
  )
}
