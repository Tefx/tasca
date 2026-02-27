/**
 * Taproom - Table index page.
 *
 * Displays all discussion tables with search, status filter,
 * and join-by-invite-code functionality.
 *
 * Design source: docs/tasca-web-uiux-v0.1.md (Taproom spec)
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
import { listTables, searchTables, batchDeleteTables, type Table, type TableStatus } from '../api/tables'
import { useAuth } from '../auth/AuthContext'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ModeIndicator } from '../components/ModeIndicator'
import { CopyButton } from '../components/CopyButton'
import '../styles/taproom.css'

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

  return date.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
  })
}

/** Return the full table ID as the invite code. */
function shortCode(id: string): string {
  return id
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
const POLL_INTERVAL_MS = 5_000

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
      className={`tr-status-pill tr-status-pill--${status}`}
      aria-label={`Status: ${status}`}
    >
      {status}
    </span>
  )
}


// =============================================================================
// Main Component
// =============================================================================

/**
 * Taproom - Table index page.
 *
 * Displays all discussion tables with search, status filter,
 * and join-by-invite-code functionality.
 *
 * @example
 * ```tsx
 * // In router
 * <Route path="/" element={<Taproom />} />
 *
 * // Or standalone
 * <Taproom />
 * ```
 */
export function Taproom() {
  const navigate = useNavigate()
  const { mode } = useAuth()
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

  // Manage mode state
  const [manageMode, setManageMode] = useState(false)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [deleteLoading, setDeleteLoading] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  // Exit manage mode when switching away from admin
  useEffect(() => {
    if (mode !== 'admin') {
      setManageMode(false)
      setSelectedIds(new Set())
    }
  }, [mode])

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

  // Manage mode: toggle selection
  const toggleSelect = useCallback((tableId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(tableId)) {
        next.delete(tableId)
      } else {
        next.add(tableId)
      }
      return next
    })
  }, [])

  // Manage mode: toggle all closed tables in current view
  const toggleSelectAll = useCallback(() => {
    const closedInView = filteredTables.filter((t) => t.status === 'closed')
    const allSelected = closedInView.every((t) => selectedIds.has(t.id))
    if (allSelected) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(closedInView.map((t) => t.id)))
    }
  }, [filteredTables, selectedIds])

  // Manage mode: exit
  const exitManageMode = useCallback(() => {
    setManageMode(false)
    setSelectedIds(new Set())
    setDeleteError(null)
  }, [])

  // Manage mode: batch delete
  const handleBatchDelete = useCallback(async () => {
    if (selectedIds.size === 0) return
    setDeleteLoading(true)
    setDeleteError(null)
    try {
      await batchDeleteTables(Array.from(selectedIds))
      setSelectedIds(new Set())
      setShowDeleteConfirm(false)
      setManageMode(false)
      refetch()
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to delete tables'
      setDeleteError(message)
      setShowDeleteConfirm(false)
    } finally {
      setDeleteLoading(false)
    }
  }, [selectedIds, refetch])

  return (
    <div className="tr">
      <header className="tr-header">
        <div className="tr-header-row">
          <div className="tr-header-title">
            <h1 className="tr-title">Taproom</h1>
            <p className="tr-subtitle">Discussion tables overview</p>
          </div>
          <div className="tr-header-actions">
            {mode === 'admin' && !manageMode && (
              <button
                type="button"
                className="tr-manage-btn"
                onClick={() => setManageMode(true)}
              >
                Manage
              </button>
            )}
            {manageMode && (
              <button
                type="button"
                className="tr-manage-btn tr-manage-btn--active"
                onClick={exitManageMode}
              >
                Done
              </button>
            )}
            <ModeIndicator />
          </div>
        </div>
      </header>

      {/* Toolbar: search + filter + join */}
      <div className="tr-toolbar">
        <div className="tr-toolbar-left">
          {/* Search input */}
          <div className="tr-search">
            <label htmlFor="tr-search-input" className="sr-only">
              Search tables
            </label>
            <input
              id="tr-search-input"
              type="search"
              className="tr-search-input"
              placeholder="Search tables..."
              value={searchQuery}
              onChange={handleSearchChange}
              aria-label="Search tables by title or content"
            />
            {searchLoading && (
              <span className="tr-search-spinner" aria-label="Searching" />
            )}
          </div>

          {/* Status filter pills */}
          <div className="tr-filters" role="group" aria-label="Filter by status">
            {STATUS_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={`tr-filter-pill ${statusFilter === opt.value ? 'tr-filter-pill--active' : ''}`}
                onClick={() => setStatusFilter(opt.value)}
                aria-pressed={statusFilter === opt.value}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* Join by invite code */}
        <form className="tr-join" onSubmit={handleJoin}>
          <label htmlFor="tr-join-input" className="sr-only">
            Join by invite code
          </label>
          <input
            id="tr-join-input"
            type="text"
            className="tr-join-input"
            placeholder="Paste invite code..."
            value={joinCode}
            onChange={(e) => setJoinCode(e.target.value)}
            aria-label="Enter table invite code to join"
          />
          <button
            type="submit"
            className="tr-join-btn"
            disabled={!joinCode.trim()}
          >
            Join
          </button>
        </form>
      </div>

      {/* Content area */}
      <main className="tr-content">
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
            manageMode={manageMode}
            selectedIds={selectedIds}
            onToggleSelect={toggleSelect}
            onToggleSelectAll={toggleSelectAll}
          />
        )}
      </main>

      {/* Floating action bar for manage mode */}
      {manageMode && selectedIds.size > 0 && (
        <div className="tr-action-bar" role="status">
          <span className="tr-action-bar-count">
            {selectedIds.size} table{selectedIds.size !== 1 ? 's' : ''} selected
          </span>
          {deleteError && (
            <span className="tr-action-bar-error">{deleteError}</span>
          )}
          <button
            type="button"
            className="tr-action-bar-delete"
            onClick={() => setShowDeleteConfirm(true)}
          >
            Delete
          </button>
        </div>
      )}

      {/* Confirm dialog for batch delete */}
      <ConfirmDialog
        isOpen={showDeleteConfirm}
        title="Delete Tables"
        message={`Are you sure you want to delete ${selectedIds.size} table${selectedIds.size !== 1 ? 's' : ''}? This will permanently remove the tables and all their sayings. This action cannot be undone.`}
        variant="danger"
        confirmLabel="Delete"
        cancelLabel="Cancel"
        isLoading={deleteLoading}
        onConfirm={handleBatchDelete}
        onCancel={() => setShowDeleteConfirm(false)}
      />
    </div>
  )
}

// =============================================================================
// State Components
// =============================================================================

function LoadingState() {
  return (
    <div className="tr-state" role="status" aria-label="Loading tables">
      <div className="tr-spinner" />
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
    <div className="tr-state tr-state--error" role="alert">
      <p className="tr-state-title">Failed to load tables</p>
      <p className="tr-state-detail">{message}</p>
      <button type="button" className="tr-retry-btn" onClick={onRetry}>
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
    <div className="tr-state tr-state--empty">
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
  manageMode: boolean
  selectedIds: Set<string>
  onToggleSelect: (tableId: string) => void
  onToggleSelectAll: () => void
}

function TableList({
  tables,
  onRowClick,
  onRowKeyDown,
  manageMode,
  selectedIds,
  onToggleSelect,
  onToggleSelectAll,
}: TableListProps) {
  const closedTables = tables.filter((t) => t.status === 'closed')
  const allClosedSelected = closedTables.length > 0 && closedTables.every((t) => selectedIds.has(t.id))

  return (
    <table className="tr-table" role="grid" aria-label="Discussion tables">
      <thead>
        <tr>
          {manageMode && (
            <th scope="col" className="tr-cell-checkbox">
              <input
                type="checkbox"
                checked={allClosedSelected}
                onChange={onToggleSelectAll}
                aria-label="Select all closed tables"
                disabled={closedTables.length === 0}
              />
            </th>
          )}
          <th scope="col">Title</th>
          <th scope="col">Status</th>
          <th scope="col">Tags</th>
          <th scope="col">Participants</th>
          <th scope="col">Last Activity</th>
          <th scope="col">Invite Code</th>
        </tr>
      </thead>
      <tbody>
        {tables.map((table) => {
          const isClosed = table.status === 'closed'
          const isSelected = selectedIds.has(table.id)
          const rowDisabled = manageMode && !isClosed

          return (
            <tr
              key={table.id}
              className={`tr-table-row${rowDisabled ? ' tr-table-row--disabled' : ''}${isSelected ? ' tr-table-row--selected' : ''}`}
              onClick={() => {
                if (manageMode) {
                  if (isClosed) onToggleSelect(table.id)
                } else {
                  onRowClick(table.id)
                }
              }}
              onKeyDown={(e) => {
                if (manageMode) {
                  if ((e.key === 'Enter' || e.key === ' ') && isClosed) {
                    e.preventDefault()
                    onToggleSelect(table.id)
                  }
                } else {
                  onRowKeyDown(table.id, e)
                }
              }}
              tabIndex={rowDisabled ? -1 : 0}
              role="row"
              aria-label={`Table: ${table.question}`}
              aria-selected={manageMode ? isSelected : undefined}
              aria-disabled={rowDisabled || undefined}
            >
              {manageMode && (
                <td className="tr-cell-checkbox">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    disabled={!isClosed}
                    onChange={() => onToggleSelect(table.id)}
                    onClick={(e) => e.stopPropagation()}
                    aria-label={`Select table: ${table.question}`}
                  />
                </td>
              )}
              <td className="tr-cell-title">
                <div className="tr-cell-title-inner">
                  <span className="tr-table-question">{table.question}</span>
                  {table.context && (
                    <span className="tr-table-context">{table.context}</span>
                  )}
                </div>
              </td>
              <td>
                <StatusPill status={table.status} />
              </td>
              <td className="tr-cell-muted">&mdash;</td>
              <td className="tr-cell-muted">&mdash;</td>
              <td className="tr-cell-time">{formatTime(table.updated_at)}</td>
              <td className="tr-cell-code">
                <div className="tr-cell-code-inner">
                  <code className="tr-invite-code">{shortCode(table.id)}</code>
                  <CopyButton text={shortCode(table.id)} label="invite code" className="tr-copy-btn" />
                </div>
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
