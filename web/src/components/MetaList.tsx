/**
 * MetaList - Shared metadata list component for table info display.
 *
 * Renders a list of key-value metadata items (status, dates, version, context).
 * Used in both the HUD collapsible info section and the mobile info tab.
 */

import type { TableStatus } from '../api/tables'

// =============================================================================
// Types
// =============================================================================

export interface MetaListProps {
  /** Table status (open/paused/closed) */
  status: TableStatus
  /** ISO timestamp of table creation */
  createdAt: string
  /** ISO timestamp of last update */
  updatedAt: string
  /** Table version number */
  version: number
  /** Optional context/description */
  context?: string | null
  /** Optional additional CSS class for the <ul> element */
  className?: string
}

// =============================================================================
// Helpers
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
// Component
// =============================================================================

/**
 * MetaList renders a styled list of table metadata items.
 *
 * @example
 * <MetaList
 *   status="open"
 *   createdAt={table.created_at}
 *   updatedAt={table.updated_at}
 *   version={table.version}
 *   context={table.context}
 * />
 */
export function MetaList({
  status,
  createdAt,
  updatedAt,
  version,
  context,
  className = 'mc-meta-list',
}: MetaListProps): JSX.Element {
  return (
    <ul className={className}>
      <li className="mc-meta-item">
        <span className="mc-meta-label">Status</span>
        <span className="mc-meta-value">{statusLabel(status)}</span>
      </li>
      <li className="mc-meta-item">
        <span className="mc-meta-label">Created</span>
        <span className="mc-meta-value">{formatDate(createdAt)}</span>
      </li>
      <li className="mc-meta-item">
        <span className="mc-meta-label">Updated</span>
        <span className="mc-meta-value">{formatDate(updatedAt)}</span>
      </li>
      <li className="mc-meta-item">
        <span className="mc-meta-label">Version</span>
        <span className="mc-meta-value mc-meta-value--mono">v{version}</span>
      </li>
      {context && (
        <li className="mc-meta-item">
          <span className="mc-meta-label">Context</span>
          <span className="mc-meta-value">{context}</span>
        </li>
      )}
    </ul>
  )
}
