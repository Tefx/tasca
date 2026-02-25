/**
 * CommandConsole — Bottom command bar for the Table view.
 *
 * Contains the MentionInput, send button, and table status controls
 * (pause/resume, request summary, end meeting).
 *
 * Design source: docs/tasca-web-uiux-v0.1.md (Table View / Mission Control spec §E Controls)
 */

import { useState, useCallback, useRef, forwardRef, useImperativeHandle, useEffect } from 'react'
import { postSaying, type Saying, type Seat } from '../api/sayings'
import { pauseTable, resumeTable, closeTable, type Table as TableType } from '../api/tables'
import { MentionInput, type MentionInputRef } from './MentionInput'
import { type PatronInfo } from './SeatDeck'
import { useAuth } from '../auth/AuthContext'
import { RequestSummaryButton } from './RequestSummaryButton'

// =============================================================================
// Types
// =============================================================================

/** Ref handle exposed by CommandConsole — used for keyboard nav focus. */
export interface CommandConsoleRef {
  focus: () => void
}

export interface CommandConsoleProps {
  table: TableType
  seats: Seat[]
  patrons?: Map<string, PatronInfo>
  /** Called after a saying is successfully posted — receives the new saying for optimistic update. */
  onPosted?: (saying: Saying) => void
  /** Called when table status changes successfully */
  onStatusChange?: (table: TableType) => void
  /** Called when an error occurs */
  onError?: (error: Error) => void
}

// =============================================================================
// Helpers
// =============================================================================

/** Check if table can be paused. */
function canPause(status: string): boolean {
  return status === 'open'
}

/** Check if table can be resumed. */
function canResume(status: string): boolean {
  return status === 'paused'
}

/** Check if table can be closed. */
function canClose(status: string): boolean {
  return status === 'open' || status === 'paused'
}

// =============================================================================
// ConsoleToolbar Component
// =============================================================================

interface ConsoleToolbarProps {
  table: TableType
  seats: Seat[]
  patrons?: Map<string, PatronInfo>
  isSubmitting: boolean
  onInsertSummary: (text: string) => void
  onStatusChange?: (table: TableType) => void
  onError?: (error: Error) => void
}

/**
 * ConsoleToolbar — Admin controls for table operations.
 * Renders pause/resume/close buttons and summary request.
 */
function ConsoleToolbar({
  table,
  seats,
  patrons,
  isSubmitting,
  onInsertSummary,
  onStatusChange,
  onError,
}: ConsoleToolbarProps) {
  const [controlState, setControlState] = useState<'idle' | 'pausing' | 'resuming'>('idle')
  const [closeState, setCloseState] = useState<'idle' | 'confirming' | 'closing'>('idle')

  const isOperating = controlState !== 'idle'

  const handlePause = useCallback(async () => {
    if (!canPause(table.status) || isOperating) return

    setControlState('pausing')
    try {
      const updated = await pauseTable(table)
      onStatusChange?.(updated)
    } catch (err) {
      onError?.(err instanceof Error ? err : new Error('Failed to pause table'))
    } finally {
      setControlState('idle')
    }
  }, [table, isOperating, onStatusChange, onError])

  const handleResume = useCallback(async () => {
    if (!canResume(table.status) || isOperating) return

    setControlState('resuming')
    try {
      const updated = await resumeTable(table)
      onStatusChange?.(updated)
    } catch (err) {
      onError?.(err instanceof Error ? err : new Error('Failed to resume table'))
    } finally {
      setControlState('idle')
    }
  }, [table, isOperating, onStatusChange, onError])

  const handleClose = useCallback(() => {
    if (!canClose(table.status) || isOperating || closeState !== 'idle') return
    setCloseState('confirming')
  }, [table.status, isOperating, closeState])

  const confirmClose = useCallback(async () => {
    if (closeState !== 'confirming') return
    setCloseState('closing')
    try {
      const updated = await closeTable(table)
      onStatusChange?.(updated)
      setCloseState('idle')
    } catch (err) {
      onError?.(err instanceof Error ? err : new Error('Failed to close table'))
      setCloseState('idle')
    }
  }, [table, closeState, onStatusChange, onError])

  const cancelClose = useCallback(() => {
    setCloseState('idle')
  }, [])

  // Escape key cancels confirmation
  useEffect(() => {
    if (closeState !== 'confirming') return

    const handleDocumentKeydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        cancelClose()
      }
    }

    document.addEventListener('keydown', handleDocumentKeydown)
    return () => {
      document.removeEventListener('keydown', handleDocumentKeydown)
    }
  }, [closeState, cancelClose])

  // Auto-revert timer: cancel confirmation after 5s idle
  useEffect(() => {
    if (closeState !== 'confirming') return

    const timerId = window.setTimeout(() => {
      setCloseState('idle')
    }, 5000)

    return () => {
      window.clearTimeout(timerId)
    }
  }, [closeState])

  return (
    <div className="mc-console-toolbar">
      <RequestSummaryButton
        seats={seats}
        patrons={patrons}
        onInsert={onInsertSummary}
        disabled={table.status === 'closed'}
        isOperating={isSubmitting}
      />
      {canPause(table.status) && (
        <button
          type="button"
          className="mc-control-btn mc-control-btn--pause"
          onClick={handlePause}
          disabled={isOperating}
          title="Pause table — prevent new joins"
        >
          {controlState === 'pausing' ? 'Pausing...' : 'Pause'}
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
          {controlState === 'resuming' ? 'Resuming...' : 'Resume'}
        </button>
      )}
      {canClose(table.status) && closeState === 'idle' && (
        <button
          type="button"
          className="mc-control-btn mc-control-btn--end-ghost"
          onClick={handleClose}
          disabled={isOperating}
          title="End meeting — close table permanently"
        >
          End Meeting
        </button>
      )}
      {canClose(table.status) && closeState !== 'idle' && (
        <span className="mc-inline-confirm" role="group" aria-label="Confirm end meeting">
          <span className="mc-inline-confirm-label">End meeting?</span>
          <button
            type="button"
            className="mc-control-btn mc-control-btn--end-confirm"
            onClick={confirmClose}
            disabled={closeState === 'closing'}
            aria-label="Confirm end meeting"
          >
            {closeState === 'closing' ? 'Closing...' : 'Confirm'}
          </button>
          <button
            type="button"
            className="mc-control-btn mc-control-btn--cancel"
            onClick={cancelClose}
            disabled={closeState === 'closing'}
            aria-label="Cancel end meeting"
          >
            Cancel
          </button>
        </span>
      )}
    </div>
  )
}

// =============================================================================
// CommandConsole
// =============================================================================

/**
 * CommandConsole — Bottom command bar for the Table view.
 *
 * @example
 * // Basic usage with table and seats
 * <CommandConsole
 *   table={tableData}
 *   seats={seatsArray}
 *   onPosted={(saying) => console.log('Posted:', saying)}
 * />
 *
 * @example
 * // With keyboard nav ref
 * const consoleRef = useRef<CommandConsoleRef>(null)
 * <CommandConsole ref={consoleRef} table={tableData} seats={[]} />
 */
export const CommandConsole = forwardRef<CommandConsoleRef, CommandConsoleProps>(
  function CommandConsole({ table, seats, patrons, onPosted, onStatusChange, onError }, ref) {
    const { mode, hasToken } = useAuth()
    const [value, setValue] = useState('')
    const [isSubmitting, setIsSubmitting] = useState(false)
    const [error, setError] = useState<string | null>(null)
    const mentionInputRef = useRef<MentionInputRef>(null)

    const isAdmin = mode === 'admin' && hasToken
    const isClosed = table.status === 'closed'

    // Expose focus() to parent via forwardRef (keyboard nav '/' binding)
    useImperativeHandle(ref, () => ({
      focus: () => mentionInputRef.current?.focus(0),
    }))

    const handleSubmit = useCallback(async () => {
      const trimmed = value.trim()
      if (!trimmed || !isAdmin || isSubmitting) return

      setIsSubmitting(true)
      setError(null)
      try {
        const newSaying = await postSaying(table.id, { speaker_name: 'Human', content: trimmed, patron_id: null })
        setValue('')
        onPosted?.(newSaying)
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to send saying')
      } finally {
        setIsSubmitting(false)
      }
    }, [value, isAdmin, isSubmitting, table.id, onPosted])

    const handleInsertSummaryRequest = useCallback((text: string) => {
      setValue(text)
      mentionInputRef.current?.focus(text.length)
    }, [])

    return (
      <div className="mc-console">
        {/* Toolbar controls — only for admin */}
        {isAdmin && (
          <ConsoleToolbar
            table={table}
            seats={seats}
            patrons={patrons}
            isSubmitting={isSubmitting}
            onInsertSummary={handleInsertSummaryRequest}
            onStatusChange={onStatusChange}
            onError={onError}
          />
        )}

        {error && (
          <p className="mc-console-error" role="alert">
            {error}
          </p>
        )}
        <div className="mc-console-row">
          <MentionInput
            ref={mentionInputRef}
            value={value}
            onChange={setValue}
            seats={seats}
            patrons={patrons}
            disabled={!isAdmin || isClosed}
            onSubmit={handleSubmit}
            placeholder={
              isClosed
                ? 'Meeting ended — no further messages'
                : isAdmin
                  ? 'Say something…'
                  : 'Viewer mode — enter admin to post'
            }
            className="mc-console-input"
          />
          {isAdmin && !isClosed && (
            <button
              type="button"
              className="mc-console-send-btn"
              onClick={handleSubmit}
              disabled={!value.trim() || isSubmitting}
              title="Send saying (Enter)"
            >
              {isSubmitting ? '…' : 'Send'}
            </button>
          )}
        </div>
      </div>
    )
  }
)
