/**
 * CommandConsole — Bottom command bar for the Table view.
 *
 * Contains the MentionInput, send button, and table status controls
 * (pause/resume, request summary).
 *
 * Design source: docs/tasca-web-uiux-v0.1.md (Table View / Mission Control spec §E Controls)
 */

import { useState, useCallback, useRef } from 'react'
import { postSaying, type Saying, type Seat } from '../api/sayings'
import { pauseTable, resumeTable, type Table as TableType } from '../api/tables'
import { MentionInput, type MentionInputRef } from './MentionInput'
import { type PatronInfo } from './SeatDeck'
import { useAuth } from '../auth/AuthContext'
import { RequestSummaryButton } from './RequestSummaryButton'

// =============================================================================
// Types
// =============================================================================

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

// =============================================================================
// CommandConsole
// =============================================================================

export function CommandConsole({ table, seats, patrons, onPosted, onStatusChange, onError }: CommandConsoleProps) {
  const { mode, hasToken } = useAuth()
  const [value, setValue] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [controlState, setControlState] = useState<'idle' | 'pausing' | 'resuming'>('idle')
  const mentionInputRef = useRef<MentionInputRef>(null)

  const isAdmin = mode === 'admin' && hasToken
  const isOperating = controlState !== 'idle'

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

  const handleInsertSummaryRequest = useCallback((text: string) => {
    setValue(text)
    // Focus the input after inserting using the ref
    mentionInputRef.current?.focus(text.length)
  }, [])

  return (
    <div className="mc-console">
      {/* Toolbar controls — only for admin */}
      {isAdmin && (
        <div className="mc-console-toolbar">
          <RequestSummaryButton
            seats={seats}
            patrons={patrons}
            onInsert={handleInsertSummaryRequest}
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
        </div>
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
          disabled={!isAdmin}
          onSubmit={handleSubmit}
          placeholder={isAdmin ? 'Say something…' : 'Viewer mode — enter admin to post'}
          className="mc-console-input"
        />
        {isAdmin && (
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
