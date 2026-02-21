/**
 * TableControls — Pause/Resume/Close controls for table.
 *
 * Provides admin controls for table lifecycle management:
 * - Pause/Resume toggle
 * - Close (end meeting)
 *
 * Design source: Task web.controls_pause_resume_close
 */

import { useState, useCallback } from 'react'
import { useAuth } from '../auth/AuthContext'
import {
  pauseTable,
  resumeTable,
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

type OperationState = 'idle' | 'pausing' | 'resuming' | 'closing'

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

/** Check if table can be paused. */
function canPause(status: TableStatus): boolean {
  return status === 'open'
}

/** Check if table can be resumed. */
function canResume(status: TableStatus): boolean {
  return status === 'paused'
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

  const handlePause = useCallback(async () => {
    if (!canPause(table.status) || isOperating) return

    setOperation('pausing')
    try {
      const updated = await pauseTable(table)
      onStatusChange(updated)
    } catch (error) {
      onError?.(error instanceof Error ? error : new Error('Failed to pause table'))
    } finally {
      setOperation('idle')
    }
  }, [table, isOperating, onStatusChange, onError])

  const handleResume = useCallback(async () => {
    if (!canResume(table.status) || isOperating) return

    setOperation('resuming')
    try {
      const updated = await resumeTable(table)
      onStatusChange(updated)
    } catch (error) {
      onError?.(error instanceof Error ? error : new Error('Failed to resume table'))
    } finally {
      setOperation('idle')
    }
  }, [table, isOperating, onStatusChange, onError])

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

        {/* Control buttons — only visible in admin mode */}
        {isAdmin && (
          <div className="mc-table-controls-actions">
            {/* Pause/Resume button */}
            {canPause(table.status) && (
              <button
                type="button"
                className="mc-control-btn mc-control-btn--pause"
                onClick={handlePause}
                disabled={isOperating}
                title="Pause table — prevent new joins"
              >
                {operation === 'pausing' ? 'Pausing...' : 'Pause'}
              </button>
            )}

            {canResume(table.status) && (
              <button
                type="button"
                className="mc-control-btn mc-control-btn--resume"
                onClick={handleResume}
                disabled={isOperating}
                title="Resume table — allow new joins"
              >
                {operation === 'resuming' ? 'Resuming...' : 'Resume'}
              </button>
            )}

            {/* Close button */}
            {canClose(table.status) && (
              <button
                type="button"
                className="mc-control-btn mc-control-btn--close"
                onClick={handleClose}
                disabled={isOperating}
                title="End meeting — close table permanently"
              >
                {operation === 'closing' ? 'Closing...' : 'End Meeting'}
              </button>
            )}
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