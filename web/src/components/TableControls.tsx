/**
 * TableControls — Status indicator and download control for table HUD header.
 *
 * Shows the table status pill, a Download link, and a closed timestamp when
 * the meeting has ended.
 *
 * Close (End Meeting) control has moved to the CommandConsole toolbar.
 *
 * Design source: Task web_fix.s1-pause-resume-footer (Spec §E Controls)
 */

import { useState } from 'react'
import {
  downloadTableExport,
  type Table as TableType,
  type TableStatus,
} from '../api/tables'
import '../styles/table.css'

// =============================================================================
// Types
// =============================================================================

interface TableControlsProps {
  /** Current table state */
  table: TableType
}

// =============================================================================
// Status Helpers
// =============================================================================

/** Get human-readable status description. */
function statusDescription(status: TableStatus): string {
  switch (status) {
    case 'open':
      return 'Open for discussion'
    case 'paused':
      return 'PAUSED (soft) — no new joins'
    case 'closed':
      return 'Closed — meeting ended'
  }
}

/** Get display label for the status pill. */
function statusLabel(status: TableStatus): string {
  switch (status) {
    case 'open':
      return 'open'
    case 'paused':
      return 'PAUSED (soft)'
    case 'closed':
      return 'closed'
  }
}

// =============================================================================
// Component
// =============================================================================

/**
 * TableControls — Status indicator and download control for table HUD header.
 *
 * @example
 * // Basic usage
 * <TableControls table={tableData} />
 *
 * @example
 * // With closed table
 * <TableControls table={{ ...tableData, status: 'closed' }} />
 */
export function TableControls({ table }: TableControlsProps) {
  const [isDownloading, setIsDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)

  const handleDownload = async () => {
    if (isDownloading) return
    setIsDownloading(true)
    setDownloadError(null)
    try {
      await downloadTableExport(table.id, 'markdown')
    } catch {
      setDownloadError('Transcript download failed. Try again.')
    } finally {
      setIsDownloading(false)
    }
  }

  return (
    <div className="mc-table-controls">
      {/* Status indicator */}
      <div className="mc-table-controls-status">
        <span
          className={`mc-status-pill mc-status-pill--${table.status}`}
          title={statusDescription(table.status)}
        >
          {statusLabel(table.status)}
        </span>
      </div>

      <div className="mc-table-controls-actions">
        <button
          type="button"
          className="mc-control-btn mc-control-btn--download"
          onClick={handleDownload}
          disabled={isDownloading}
          title="Download table transcript as Markdown"
        >
          {isDownloading ? 'Downloading…' : 'Download'}
        </button>
      </div>

      {downloadError && <p role="alert">{downloadError}</p>}

      {/* Closed timestamp */}
      {table.status === 'closed' && (
        <div className="mc-table-controls-closed">
          <span className="mc-table-closed-label">Closed</span>
          <time className="mc-table-closed-time" dateTime={table.updated_at}>
            {formatClosedTime(table.updated_at)}
          </time>
        </div>
      )}
    </div>
  )
}

// =============================================================================
// Helpers
// =============================================================================

/** Format the closed timestamp. */
function formatClosedTime(iso: string): string {
  const date = new Date(iso)
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}
