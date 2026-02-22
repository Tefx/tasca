/**
 * Watchtower - Table index page.
 *
 * Displays all discussion tables with search, status filter,
 * and join-by-invite-code functionality.
 *
 * Design source: docs/tasca-web-uiux-v0.1.md (Watchtower spec)
 */

import {
  useState,
  useEffect,
  useCallback,
  useRef,
  type FormEvent,
  type ChangeEvent,
} from 'react'
import { useNavigate } from 'react-router-dom'
import { listTables, searchTables, type Table, type TableStatus } from '../api/tables'
import { ModeIndicator } from '../components/ModeIndicator'
import '../styles/watchtower.css'

// =============================================================================
// Constants
// =============================================================================

const STATUS_OPTIONS: Array<{ value: TableStatus | 'all'; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'open', label: 'Open' },
  { value: 'paused', label: 'Paused' },
  { value: 'closed', label: 'Closed' },
]

// =============================================================================
// Utility Functions
// =============================================================================

/** Format an ISO date string to a human-readable relative or absolute time. */
function formatTime(iso: string): string {
  const date = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMinutes = Math.floor(diffMs / 60_000)
  const diffHours = Math.floor(diffMs / 3_600_000)
  const diffDays = Math.floor(diffMs / 86_400_000)

  if (diffMinutes < 1) return 'just now'
  if (diffMinutes < 60) return `${diffMinutes}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`

  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
  })
}

/** Get the first 8 characters of an ID as a placeholder invite code. */
function shortCode(id: string): string {
  return id.slice(0, 8)
}

// =============================================================================
// Hooks
// =============================================================================

interface UseTablesResult {
  tables: Table[]
  loading: boolean
  error: string | null
  refetch: () => void
}

/** Poll interval for background table list refresh (ms). */
const POLL_INTERVAL_MS = 15_000

function useTables(): UseTablesResult {
  const [tables, setTables] = useState<Table[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetchTables = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listTables()
      setTables(data)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load tables'
      setError(message)
    } finally {
      setLoading(false)
    }
  }, [])

  // Initial load
  useEffect(() => {
    fetchTables()
  }, [fetchTables])

  // Background poll — silently refresh without resetting loading state
  useEffect(() => {
    const poll = async () => {
      try {
        const data = await listTables()
        setTables(data)
        setError(null)
      } catch {
        // Ignore poll errors — user can retry manually
      }
    }
    const id = setInterval(poll, POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [])

  return { tables, loading, error, refetch: fetchTables }
}

// =============================================================================
// Sub-Components
// =============================================================================

interface StatusPillProps {
  status: TableStatus
}

function StatusPill({ status }: StatusPillProps) {
  return (
    <span
      className={`wt-status-pill wt-status-pill--${status}`}
      aria-label={`Status: ${status}`}
    >
      {status}
    </span>
  )
}

interface CopyButtonProps {
  text: string
}

function CopyButton({ text }: CopyButtonProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Fallback: select text approach not needed for v0.1
    }
  }, [text])

  return (
    <button
      className="wt-copy-btn"
      onClick={(e) => {
        e.stopPropagation()
        handleCopy()
      }}
      aria-label={copied ? 'Copied' : `Copy code ${text}`}
      title={copied ? 'Copied!' : 'Copy to clipboard'}
      type="button"
    >
      {copied ? 'done' : 'copy'}
    </button>
  )
}

// =============================================================================
// Main Component
// =============================================================================

export function Watchtower() {
  const navigate = useNavigate()
  const { tables, loading, error, refetch } = useTables()

  // Search state
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<Table[] | null>(null)
  const [searchLoading, setSearchLoading] = useState(false)
  const searchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Filter state
  const [statusFilter, setStatusFilter] = useState<TableStatus | 'all'>('all')

  // Join-by-code state
  const [joinCode, setJoinCode] = useState('')

  // Derived: which tables to display
  const displayTables = searchResults ?? tables
  const filteredTables =
    statusFilter === 'all'
      ? displayTables
      : displayTables.filter((t) => t.status === statusFilter)

  // Search handler with debounce
  const handleSearchChange = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      const query = e.target.value
      setSearchQuery(query)

      if (searchTimeoutRef.current) {
        clearTimeout(searchTimeoutRef.current)
      }

      if (!query.trim()) {
        setSearchResults(null)
        setSearchLoading(false)
        return
      }

      setSearchLoading(true)
      searchTimeoutRef.current = setTimeout(async () => {
        try {
          const response = await searchTables(query.trim())
          // Convert SearchHit to Table-like objects for display
          const mapped: Table[] = response.hits.map((hit) => ({
            id: hit.table_id,
            question: hit.question,
            context: null,
            status: hit.status as TableStatus,
            version: 0,
            created_at: hit.created_at,
            updated_at: hit.updated_at,
          }))
          setSearchResults(mapped)
        } catch {
          // On search error, fall back to client-side filter
          setSearchResults(
            tables.filter((t) =>
              t.question.toLowerCase().includes(query.toLowerCase())
            )
          )
        } finally {
          setSearchLoading(false)
        }
      }, 300)
    },
    [tables]
  )

  // Clean up timeout on unmount
  useEffect(() => {
    return () => {
      if (searchTimeoutRef.current) {
        clearTimeout(searchTimeoutRef.current)
      }
    }
  }, [])

  // Join by invite code
  const handleJoin = useCallback(
    (e: FormEvent) => {
      e.preventDefault()
      const code = joinCode.trim()
      if (!code) return

      // Try to find a table whose ID starts with the code
      const match = tables.find((t) => t.id.startsWith(code))
      if (match) {
        navigate(`/tables/${match.id}`)
      } else {
        // Assume the code IS a full table ID and navigate directly
        navigate(`/tables/${code}`)
      }
    },
    [joinCode, tables, navigate]
  )

  // Navigate to table on row click
  const handleRowClick = useCallback(
    (tableId: string) => {
      navigate(`/tables/${tableId}`)
    },
    [navigate]
  )

  const handleRowKeyDown = useCallback(
    (tableId: string, e: React.KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        navigate(`/tables/${tableId}`)
      }
    },
    [navigate]
  )

  return (
    <div className="wt">
      <header className="wt-header">
        <div className="wt-header-row">
          <div className="wt-header-title">
            <h1 className="wt-title">Watchtower</h1>
            <p className="wt-subtitle">Discussion tables overview</p>
          </div>
          <ModeIndicator />
        </div>
      </header>

      {/* Toolbar: search + filter + join */}
      <div className="wt-toolbar">
        <div className="wt-toolbar-left">
          {/* Search input */}
          <div className="wt-search">
            <label htmlFor="wt-search-input" className="sr-only">
              Search tables
            </label>
            <input
              id="wt-search-input"
              type="search"
              className="wt-search-input"
              placeholder="Search tables..."
              value={searchQuery}
              onChange={handleSearchChange}
              aria-label="Search tables by title or content"
            />
            {searchLoading && (
              <span className="wt-search-spinner" aria-label="Searching" />
            )}
          </div>

          {/* Status filter pills */}
          <div className="wt-filters" role="group" aria-label="Filter by status">
            {STATUS_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={`wt-filter-pill ${statusFilter === opt.value ? 'wt-filter-pill--active' : ''}`}
                onClick={() => setStatusFilter(opt.value)}
                aria-pressed={statusFilter === opt.value}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* Join by invite code */}
        <form className="wt-join" onSubmit={handleJoin}>
          <label htmlFor="wt-join-input" className="sr-only">
            Join by invite code
          </label>
          <input
            id="wt-join-input"
            type="text"
            className="wt-join-input"
            placeholder="Paste invite code..."
            value={joinCode}
            onChange={(e) => setJoinCode(e.target.value)}
            aria-label="Enter table invite code to join"
          />
          <button
            type="submit"
            className="wt-join-btn"
            disabled={!joinCode.trim()}
          >
            Join
          </button>
        </form>
      </div>

      {/* Content area */}
      <main className="wt-content">
        {loading && <LoadingState />}
        {error && <ErrorState message={error} onRetry={refetch} />}
        {!loading && !error && filteredTables.length === 0 && (
          <EmptyState hasSearch={!!searchQuery} hasFilter={statusFilter !== 'all'} />
        )}
        {!loading && !error && filteredTables.length > 0 && (
          <TableList
            tables={filteredTables}
            onRowClick={handleRowClick}
            onRowKeyDown={handleRowKeyDown}
          />
        )}
      </main>
    </div>
  )
}

// =============================================================================
// State Components
// =============================================================================

function LoadingState() {
  return (
    <div className="wt-state" role="status" aria-label="Loading tables">
      <div className="wt-spinner" />
      <p>Loading tables...</p>
    </div>
  )
}

interface ErrorStateProps {
  message: string
  onRetry: () => void
}

function ErrorState({ message, onRetry }: ErrorStateProps) {
  return (
    <div className="wt-state wt-state--error" role="alert">
      <p className="wt-state-title">Failed to load tables</p>
      <p className="wt-state-detail">{message}</p>
      <button type="button" className="wt-retry-btn" onClick={onRetry}>
        Retry
      </button>
    </div>
  )
}

interface EmptyStateProps {
  hasSearch: boolean
  hasFilter: boolean
}

function EmptyState({ hasSearch, hasFilter }: EmptyStateProps) {
  let message = 'No tables yet. Create one to get started.'
  if (hasSearch && hasFilter) {
    message = 'No tables match your search and filter.'
  } else if (hasSearch) {
    message = 'No tables match your search.'
  } else if (hasFilter) {
    message = 'No tables with this status.'
  }

  return (
    <div className="wt-state wt-state--empty">
      <p>{message}</p>
    </div>
  )
}

// =============================================================================
// Table List
// =============================================================================

interface TableListProps {
  tables: Table[]
  onRowClick: (tableId: string) => void
  onRowKeyDown: (tableId: string, e: React.KeyboardEvent) => void
}

function TableList({ tables, onRowClick, onRowKeyDown }: TableListProps) {
  return (
    <table className="wt-table" role="grid" aria-label="Discussion tables">
      <thead>
        <tr>
          <th scope="col">Title</th>
          <th scope="col">Status</th>
          <th scope="col">Tags</th>
          <th scope="col">Participants</th>
          <th scope="col">Last Activity</th>
          <th scope="col">Invite Code</th>
        </tr>
      </thead>
      <tbody>
        {tables.map((table) => (
          <tr
            key={table.id}
            className="wt-table-row"
            onClick={() => onRowClick(table.id)}
            onKeyDown={(e) => onRowKeyDown(table.id, e)}
            tabIndex={0}
            role="row"
            aria-label={`Table: ${table.question}`}
          >
            <td className="wt-cell-title">
              <span className="wt-table-question">{table.question}</span>
              {table.context && (
                <span className="wt-table-context">{table.context}</span>
              )}
            </td>
            <td>
              <StatusPill status={table.status} />
            </td>
            <td className="wt-cell-muted">&mdash;</td>
            <td className="wt-cell-muted">&mdash;</td>
            <td className="wt-cell-time">{formatTime(table.updated_at)}</td>
            <td className="wt-cell-code">
              <code className="wt-invite-code">{shortCode(table.id)}</code>
              <CopyButton text={shortCode(table.id)} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
