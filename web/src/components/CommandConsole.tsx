/**
 * CommandConsole — Bottom command bar for the Table view.
 *
 * Contains the MentionInput, send button, and table status controls
 * (pause/resume, request summary).
 *
 * Design source: docs/tasca-web-uiux-v0.1.md (Table View / Mission Control spec §E Controls)
 */

import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { postSaying, type Seat } from '../api/sayings'
import { pauseTable, resumeTable, type Table as TableType } from '../api/tables'
import { MentionInput, type MentionInputRef } from './MentionInput'
import { getPresenceStatus, type PatronInfo } from './SeatDeck'
import { useAuth } from '../auth/AuthContext'

// =============================================================================
// Types
// =============================================================================

export interface CommandConsoleProps {
  table: TableType
  seats: Seat[]
  patrons?: Map<string, PatronInfo>
  /** Called after a saying is successfully posted (to trigger stream refresh). */
  onPosted?: () => void
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

/** Default summary request template. */
const SUMMARY_REQUEST_TEMPLATE =
  '@{target} Please provide a summary of the discussion so far. Key points, decisions made, and any open questions or next steps would be helpful.'

// =============================================================================
// Request Summary Button
// =============================================================================

interface RequestSummaryButtonProps {
  seats: Seat[]
  patrons?: Map<string, PatronInfo>
  onInsert: (text: string) => void
  disabled?: boolean
  isOperating?: boolean
}

/**
 * Request Summary button with patron picker.
 *
 * Per spec §E Controls:
 * - Opens a picker to select target patron
 * - Defaults to first agent/host
 * - Inserts standardized summary request saying
 */
function RequestSummaryButton({ seats, patrons, onInsert, disabled, isOperating }: RequestSummaryButtonProps) {
  const [isOpen, setIsOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  // Filter to active (non-offline) agent seats, prioritized as "hosts"
  const selectablePatrons = useMemo(() => {
    const activeSeats = seats.filter((seat) => {
      if (seat.state !== 'joined') return false
      const presenceStatus = getPresenceStatus(seat.last_heartbeat)
      return presenceStatus !== 'offline'
    })

    // Get unique patrons (one seat per patron)
    const patronMap = new Map<string, { seat: Seat; patron?: PatronInfo }>()
    for (const seat of activeSeats) {
      if (!patronMap.has(seat.patron_id)) {
        const patron = patrons?.get(seat.patron_id)
        // Prefer agents (hosts) over humans
        if (patron?.kind === 'agent' || !patron) {
          patronMap.set(seat.patron_id, { seat, patron })
        }
      }
    }

    // If no agents, include humans
    if (patronMap.size === 0) {
      for (const seat of activeSeats) {
        if (!patronMap.has(seat.patron_id)) {
          const patron = patrons?.get(seat.patron_id)
          patronMap.set(seat.patron_id, { seat, patron })
        }
      }
    }

    return Array.from(patronMap.values())
  }, [seats, patrons])

  // Get default patron (first agent/host)
  const defaultPatron = useMemo(() => {
    return selectablePatrons.find(({ patron }) => patron?.kind === 'agent') ?? selectablePatrons[0]
  }, [selectablePatrons])

  // Close picker on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false)
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isOpen])

  const handleSelect = (target: { patronId: string; displayName: string; isDefault: boolean }) => {
    const text = SUMMARY_REQUEST_TEMPLATE.replace('{target}', target.displayName)
    onInsert(text)
    setIsOpen(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setIsOpen(false)
    }
  }

  if (selectablePatrons.length === 0) {
    return null
  }

  const isDisabled = disabled || isOperating

  return (
    <div ref={containerRef} className="mc-summary-picker-wrapper">
      <button
        type="button"
        className="mc-control-btn mc-control-btn--summary"
        onClick={() => setIsOpen(!isOpen)}
        disabled={isDisabled}
        title="Request a summary from a participant"
        aria-expanded={isOpen}
        aria-haspopup="listbox"
      >
        {isOperating ? '...' : 'Request Summary'}
      </button>

      {isOpen && !isDisabled && (
        <div
          className="mc-summary-picker"
          role="listbox"
          aria-label="Select participant to request summary from"
          onKeyDown={handleKeyDown}
        >
          <div className="mc-summary-picker-header">Select target</div>
          {selectablePatrons.map(({ seat, patron }) => {
            const displayName = patron?.name ?? seat.patron_id
            const patronKind = patron?.kind ?? 'agent'
            const isDefault = defaultPatron?.seat.patron_id === seat.patron_id
            const presenceStatus = getPresenceStatus(seat.last_heartbeat)

            return (
              <button
                key={seat.patron_id}
                type="button"
                className={`mc-summary-picker-item ${isDefault ? 'mc-summary-picker-item--default' : ''}`}
                role="option"
                onClick={() => handleSelect({
                  patronId: seat.patron_id,
                  displayName,
                  isDefault,
                })}
                aria-selected={isDefault}
              >
                <div className={`mc-mention-avatar mc-mention-avatar--${patronKind}`}>
                  {displayName.slice(0, 2).toUpperCase()}
                </div>
                <div className="mc-mention-info">
                  <span className="mc-mention-name">{displayName}</span>
                  <span className="mc-mention-meta">
                    <span className={`mc-mention-presence mc-mention-presence--${presenceStatus}`} />
                    {patronKind}
                  </span>
                </div>
                {isDefault && <span className="mc-summary-picker-item-badge">Default</span>}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
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
      await postSaying(table.id, { speaker_name: 'Human', content: trimmed, patron_id: null })
      setValue('')
      onPosted?.()
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

      {/* Footer controls — only for admin */}
      {isAdmin && (
        <div className="mc-console-controls">
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
    </div>
  )
}
