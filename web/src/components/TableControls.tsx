/**
 * TableControls — Status indicator and Close control for table HUD header.
 *
 * Provides status display and Close (end meeting) control.
 * Pause/Resume controls are now in the footer console area.
 *
 * Design source: Task web_fix.s1-pause-resume-footer (Spec §E Controls)
 */

import { useState, useCallback } from 'react'
import { useAuth } from '../auth/AuthContext'
import {
  closeTable,
  type Table as TableType,
  type TableStatus,
} from '../api/tables'
import { ConfirmDialog } from './ConfirmDialog'
import '../styles/table.css'

// =============================================================================
// Types
// =============================================================================

interface TableControlsProps {
  /** Current table state */
  table: TableType
  /** Called when table status changes successfully */
  onStatusChange: (table: TableType) => void
  /** Called when an error occurs */
  onError?: (error: Error) => void
}

type OperationState = 'idle' | 'closing'

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

/** Check if table can be closed. */
function canClose(status: TableStatus): boolean {
  return status === 'open' || status === 'paused'
}

// =============================================================================
// Component
// =============================================================================

export function TableControls({
  table,
  onStatusChange,
  onError,
}: TableControlsProps) {
  const { mode, hasToken } = useAuth()
  const [operation, setOperation] = useState<OperationState>('idle')
  const [showCloseDialog, setShowCloseDialog] = useState(false)

  const isAdmin = mode === 'admin' && hasToken
  const isOperating = operation !== 'idle'

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  const handleClose = useCallback(async () => {
    if (!canClose(table.status) || isOperating) return

    setShowCloseDialog(true)
  }, [table.status, isOperating])

  const confirmClose = useCallback(async () => {
    setOperation('closing')
    setShowCloseDialog(false)
    try {
      const updated = await closeTable(table)
      onStatusChange(updated)
    } catch (error) {
      onError?.(error instanceof Error ? error : new Error('Failed to close table'))
    } finally {
      setOperation('idle')
    }
  }, [table, onStatusChange, onError])

  const cancelClose = useCallback(() => {
    setShowCloseDialog(false)
  }, [])

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <>
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

        {/* Close button — only visible in admin mode */}
        {isAdmin && canClose(table.status) && (
          <div className="mc-table-controls-actions">
            <button
              type="button"
              className="mc-control-btn mc-control-btn--close"
              onClick={handleClose}
              disabled={isOperating}
              title="End meeting — close table permanently"
            >
              {operation === 'closing' ? 'Closing...' : 'End Meeting'}
            </button>
          </div>
        )}

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

      {/* Close confirmation dialog */}
      <ConfirmDialog
        isOpen={showCloseDialog}
        title="End Meeting?"
        message={
          <p>
            This will close the table permanently. No further sayings or joins
            will be allowed. This action cannot be undone.
          </p>
        }
        confirmLabel="End Meeting"
        cancelLabel="Cancel"
        variant="danger"
        onConfirm={confirmClose}
        onCancel={cancelClose}
        isLoading={operation === 'closing'}
      />
    </>
  )
}

// =============================================================================
// Helpers
// =============================================================================

/** Format the closed timestamp. */
function formatClosedTime(iso: string): string {
  const date = new Date(iso)
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}