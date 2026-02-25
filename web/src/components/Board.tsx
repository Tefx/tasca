// DEPRECATED: Board content moved to HUD collapsible. Kept for reference.
/**
 * Board component — Context rail for Mission Control.
 *
 * Displays table metadata and board data (pinned items / agenda).
 * Board data is a v0.1 placeholder — backend does not serve board keys yet.
 *
 * Design source: docs/tasca-web-uiux-v0.1.md (Context Rail spec)
 */

import type { Table, TableStatus } from '../api/tables'

// =============================================================================
// Types
// =============================================================================

interface BoardProps {
  /** The table to display metadata for */
  table: Table
}

// =============================================================================
// Utility
// =============================================================================

/** Format an ISO date string to a locale-friendly display. */
function formatDate(iso: string): string {
  const date = new Date(iso)
  return date.toLocaleDateString(undefined, {
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
 * Board component — Context rail for Mission Control.
 *
 * Displays table metadata and board data (pinned items / agenda).
 * Board data is a v0.1 placeholder — backend does not serve board keys yet.
 *
 * @example
 * ```tsx
 * <Board table={table} />
 * ```
 */
export function Board({ table }: BoardProps) {
  return (
    <div className="mc-board" role="complementary" aria-label="Table context">
      {/* Board data section — placeholder for v0.1 */}
      <section className="mc-board-section">
        <h3 className="mc-board-section-title">Board</h3>
        <p className="mc-board-empty">No board data</p>
      </section>

      {/* Table metadata section */}
      <section className="mc-board-section">
        <h3 className="mc-board-section-title">Table Info</h3>
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
      </section>
    </div>
  )
}
