/**
 * Table — Mission Control page.
 *
 * Displays a single discussion table with two-column layout:
 * Stream (sayings) | SeatDeck (presence).
 *
 * Table metadata is shown in a collapsible "Info" section in the HUD.
 *
 * Data fetching:
 *   - Table metadata + seats: fetched once on mount.
 *   - Sayings: live via useSayingsStream long-poll (web.long_poll_stream step).
 *
 * Design source: docs/tasca-web-uiux-v0.1.md (Table View / Mission Control spec)
 */

import { useState, useEffect, useCallback, useMemo } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getTable, type Table as TableType, type TableStatus } from '../api/tables'
import { listSeats, type Seat } from '../api/sayings'
import { useSayingsStream, type ConnectionStatus } from '../hooks/useLongPoll'
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
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'loaded'; data: StaticTableData }

// =============================================================================
// Hooks
// =============================================================================

/** Poll interval for background seat refresh (ms). */
const SEAT_POLL_INTERVAL_MS = 8_000

/**
 * Fetch the static parts of the table page: table metadata and seats.
 *
 * Sayings are intentionally excluded — they are handled by useSayingsStream
 * which subscribes to the long-poll endpoint for real-time updates.
 *
 * Seats are refreshed silently every SEAT_POLL_INTERVAL_MS so new
 * participants become visible without a manual reload.
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

  // Background seat refresh — silently update who's at the table
  useEffect(() => {
    if (!tableId) return
    const poll = async () => {
      try {
        const seatsResponse = await listSeats(tableId)
        setState((prev) => {
          if (prev.status !== 'loaded') return prev
          return {
            ...prev,
            data: {
              ...prev.data,
              seats: seatsResponse.seats,
            },
          }
        })
      } catch {
        // Ignore poll errors — stale seat list is acceptable
      }
    }
    const id = setInterval(poll, SEAT_POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [tableId])

  return { state, refetch: fetchAll }
}

// =============================================================================
// Utility (formatting helpers moved from Board.tsx)
// =============================================================================

/** Format an ISO date string to a locale-friendly display. */
function formatDate(iso: string): string {
  const date = new Date(iso)
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** Get human-readable status label. */
function statusLabel(status: TableStatus): string {
  switch (status) {
    case 'open':
      return 'Open'
    case 'paused':
      return 'Paused'
    case 'closed':
      return 'Closed'
  }
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
// =============================================================================
// Connection Warning Banner
// =============================================================================

interface ConnectionWarningBannerProps {
  status: ConnectionStatus
}

function ConnectionWarningBanner({ status }: ConnectionWarningBannerProps) {
  if (status === 'live') return null

  return (
    <div
      className={`mc-connection-banner mc-connection-banner--${status}`}
      role="status"
      aria-live="polite"
    >
      {status === 'connecting'
        ? 'Reconnecting to stream...'
        : 'Stream disconnected. Check your network connection.'}
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
      {/* Tier 1: Navigation + Title (full-width, prominent) */}
      <div className="mc-hud-tier1">
        <Link to="/" className="mc-hud-back" aria-label="Back to Watchtower">
          &larr; Watchtower
        </Link>
        <h1 
          className="mc-hud-title" 
          title={table.question.length > 50 ? table.question : undefined}
        >
          {table.question}
        </h1>
      </div>

      {/* Tier 2: Controls + Metadata + Actions + Mode */}
      <div className="mc-hud-tier2">
        <TableControls table={table} onStatusChange={onStatusChange} />
        <details className="mc-hud-meta-details">
          <summary className="mc-hud-meta-summary">Info</summary>
          <ul className="mc-meta-list">
            <li className="mc-meta-item">
              <span className="mc-meta-label">Status</span>
              <span className="mc-meta-value">{statusLabel(table.status)}</span>
            </li>
            <li className="mc-meta-item">
              <span className="mc-meta-label">Created</span>
              <span className="mc-meta-value">{formatDate(table.created_at)}</span>
            </li>
            <li className="mc-meta-item">
              <span className="mc-meta-label">Updated</span>
              <span className="mc-meta-value">{formatDate(table.updated_at)}</span>
            </li>
            <li className="mc-meta-item">
              <span className="mc-meta-label">Version</span>
              <span className="mc-meta-value mc-meta-value--mono">v{table.version}</span>
            </li>
            {table.context && (
              <li className="mc-meta-item">
                <span className="mc-meta-label">Context</span>
                <span className="mc-meta-value">{table.context}</span>
              </li>
            )}
          </ul>
        </details>
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
      </div>
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

  // Optimistic table state — set by controls (pause/resume/close), cleared
  // when the long-poll stream confirms the server-side state.
  const [optimisticTable, setOptimisticTable] = useState<TableType | null>(null)

  // Live data: sayings stream via long-poll.
  const {
    sayings,
    table: streamTable,
    connectionStatus,
    appendSaying,
  } = useSayingsStream(tableId)

  // ---------------------------------------------------------------------------
  // Handle status changes from TableControls
  // ---------------------------------------------------------------------------

  const handleStatusChange = useCallback((updatedTable: TableType) => {
    setOptimisticTable(updatedTable)
  }, [])

  // ---------------------------------------------------------------------------
  // Handle errors from console controls
  // ---------------------------------------------------------------------------

  const handleError = useCallback((error: Error) => {
    // For now, just log the error. Could be enhanced with toast notifications.
    console.error('Table control error:', error.message)
  }, [])

  // ---------------------------------------------------------------------------
  // Clear optimistic override once the stream confirms the new state.
  // When the server pushes an updated table via long-poll, it becomes the
  // source of truth and the optimistic value is no longer needed.
  // ---------------------------------------------------------------------------

  useEffect(() => {
    if (streamTable && optimisticTable) {
      // Stream caught up — server confirmed the state change.
      setOptimisticTable(null)
    }
  }, [streamTable, optimisticTable])

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

  const { seats } = state.data

  // Table priority: optimistic (brief, until stream confirms) > stream > static.
  // Optimistic is set by user actions (pause/resume/close) and cleared once
  // the long-poll stream delivers the server-confirmed table state.
  const table = optimisticTable ?? streamTable ?? state.data.table

  return (
    <div className="mc">
      <Hud table={table} onStatusChange={handleStatusChange} />
      <ConnectionWarningBanner status={connectionStatus} />
      <div className="mc-columns">
        <div className="mc-col-center">
          <Stream sayings={sayings} connectionStatus={connectionStatus} tableStatus={table.status} />
          <CommandConsole
            table={table}
            seats={seats}
            patrons={patronsMap}
            onPosted={appendSaying}
            onStatusChange={handleStatusChange}
            onError={handleError}
          />
        </div>
        <SeatDeck seats={seats} patrons={patronsMap} />
      </div>
    </div>
  )
}
