/**
 * RequestSummaryButton — Patron picker that inserts a standardised summary-request saying.
 *
 * Extracted from CommandConsole.tsx.
 * Design source: docs/tasca-web-uiux-v0.1.md (Table View / Mission Control spec §E Controls)
 */

import React, { useState, useEffect, useRef, useMemo } from 'react'
import { type Seat } from '../api/sayings'
import { getPresenceStatus, type PatronInfo } from './SeatDeck'

// =============================================================================
// Constants
// =============================================================================

/** Default summary request template. */
export const SUMMARY_REQUEST_TEMPLATE =
  '@{target} Please provide a summary of the discussion so far. Key points, decisions made, and any open questions or next steps would be helpful.'

// =============================================================================
// Types
// =============================================================================

export interface RequestSummaryButtonProps {
  seats: Seat[]
  patrons?: Map<string, PatronInfo>
  onInsert: (text: string) => void
  disabled?: boolean
  isOperating?: boolean
}

// =============================================================================
// Component
// =============================================================================

/**
 * Request Summary button with patron picker.
 *
 * Per spec §E Controls:
 * - Opens a picker to select target patron
 * - Defaults to first agent/host
 * - Inserts standardized summary request saying
 *
 * @example
 * ```tsx
 * <RequestSummaryButton
 *   seats={seats}
 *   patrons={patronsMap}
 *   onInsert={(text) => postSaying(tableId, { content: text })}
 *   disabled={!isAdmin}
 * />
 * ```
 */
export function RequestSummaryButton({
  seats,
  patrons,
  onInsert,
  disabled,
  isOperating,
}: RequestSummaryButtonProps) {
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
                onClick={() =>
                  handleSelect({
                    patronId: seat.patron_id,
                    displayName,
                    isDefault,
                  })
                }
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
