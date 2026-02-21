/**
 * MentionInput component — Text input with @mention picker support.
 *
 * Triggers a mention picker dropdown when "@" is typed.
 * Supports fuzzy search/filter by participant name.
 * Handles keyboard navigation and selection.
 *
 * Design source: docs/tasca-web-uiux-v0.1.md (Seat Deck - Mention targeting)
 */

import { useState, useRef, useCallback, useEffect, useMemo } from 'react'
import { MentionPicker } from './SeatDeck'
import type { PatronInfo } from './SeatDeck'
import type { Seat } from '../api/sayings'

// =============================================================================
// Types
// =============================================================================

export interface MentionInputProps {
  /** Current input value */
  value: string
  /** Callback when value changes */
  onChange: (value: string) => void
  /** Placeholder text */
  placeholder?: string
  /** Seats available for mention */
  seats: Seat[]
  /** Patron data map */
  patrons?: Map<string, PatronInfo>
  /** Whether input is disabled */
  disabled?: boolean
  /** Additional className */
  className?: string
  /** Callback when Enter is pressed (for submitting) */
  onSubmit?: () => void
  /** Callback when a mention is inserted (for tracking/validation) */
  onMentionInsert?: (mention: { patronId: string; displayName: string }) => void
  /** Called when ambiguous mention is detected (multiple matches) */
  onAmbiguousMention?: (handle: string, candidates: PatronInfo[]) => void
  /** Called when unresolved mention is detected (no matches) */
  onUnresolvedMention?: (handle: string) => void
}

/** Position of the mention trigger in the input. */
interface MentionTrigger {
  /** Index where "@" was typed */
  startIndex: number
  /** Current filter text (after "@") */
  filter: string
}

// =============================================================================
// Utility Functions
// =============================================================================

/**
 * Extract the mention filter from the current input value and cursor position.
 * Returns null if cursor is not within a mention context.
 */
function getMentionTrigger(value: string, cursorIndex: number): MentionTrigger | null {
  // Find the "@" before the cursor
  let atIndex = -1
  for (let i = cursorIndex - 1; i >= 0; i--) {
    const char = value[i]
    if (char === '@') {
      atIndex = i
      break
    }
    // Stop if we hit whitespace (mention context ends)
    if (/\s/.test(char)) {
      break
    }
  }

  if (atIndex === -1) return null

  // Extract filter (text after "@" up to cursor)
  const filter = value.slice(atIndex + 1, cursorIndex)

  // Check if filter contains spaces (invalid mention)
  if (/\s/.test(filter)) return null

  return { startIndex: atIndex, filter }
}

/**
 * Calculate the position for the mention picker dropdown.
 */
function getPickerPosition(
  inputRef: React.RefObject<HTMLTextAreaElement | null>
): { top: number; left: number } {
  const input = inputRef.current

  if (!input) {
    return { top: 0, left: 0 }
  }

  // Default to showing below the input
  const inputRect = input.getBoundingClientRect()

  return {
    top: inputRect.height + 4,
    left: 0,
  }
}

// =============================================================================
// Main Component
// =============================================================================

export function MentionInput({
  value,
  onChange,
  placeholder = 'Say something…',
  seats,
  patrons,
  disabled = false,
  className = '',
  onSubmit,
  onMentionInsert,
}: MentionInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  // Mention picker state
  const [mentionTrigger, setMentionTrigger] = useState<MentionTrigger | null>(null)
  const [pickerPosition, setPickerPosition] = useState({ top: 0, left: 0 })

  // Filter active seats for mention picker
  const activeSeats = useMemo(() => {
    return seats.filter((seat) => {
      if (seat.state !== 'joined') return false
      // Offline check - same threshold as SeatDeck
      const lastHeartbeat = new Date(seat.last_heartbeat)
      const now = new Date()
      const diffSeconds = (now.getTime() - lastHeartbeat.getTime()) / 1000
      return diffSeconds <= 60 // Same as IDLE_THRESHOLD_SECONDS
    })
  }, [seats])

  // Handle text changes and detect @mentions
  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      const newValue = e.target.value
      const cursorIndex = e.target.selectionStart ?? newValue.length

      onChange(newValue)

      // Check for mention trigger
      const trigger = getMentionTrigger(newValue, cursorIndex)
      setMentionTrigger(trigger)

      // Update picker position
      if (trigger) {
        setPickerPosition(getPickerPosition(textareaRef))
      }
    },
    [onChange]
  )

  // Handle selection from mention picker
  const handleMentionSelect = useCallback(
    (participant: { patronId: string; displayName: string }) => {
      if (!mentionTrigger) return

      const { startIndex } = mentionTrigger

      // Replace the "@filter" with "@displayName"
      // We use the patron_id for reliable mention resolution on the backend
      const mentionText = `@${participant.displayName} `
      const before = value.slice(0, startIndex)
      const after = value.slice(startIndex + mentionTrigger.filter.length + 1) // +1 for "@"

      onChange(before + mentionText + after)

      // Reset picker
      setMentionTrigger(null)

      // Notify parent
      onMentionInsert?.(participant)

      // Focus input and position cursor after mention
      setTimeout(() => {
        if (textareaRef.current) {
          const newCursorPos = before.length + mentionText.length
          textareaRef.current.focus()
          textareaRef.current.setSelectionRange(newCursorPos, newCursorPos)
        }
      }, 0)
    },
    [mentionTrigger, value, onChange, onMentionInsert]
  )

  // Close picker on click outside
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setMentionTrigger(null)
      }
    }

    if (mentionTrigger) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [mentionTrigger])

  // Handle keyboard navigation
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // If picker is open
      if (mentionTrigger) {
        if (e.key === 'Escape') {
          e.preventDefault()
          setMentionTrigger(null)
          return
        }

        // Arrow keys for navigation
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault()
          // Note: We'd need filtered count from MentionPicker
          // For now, just prevent default
          return
        }

        // Enter to select first match
        if (e.key === 'Enter' && !e.shiftKey) {
          e.preventDefault()
          // Selection is handled by MentionPicker
          return
        }

        // Tab to select first match
        if (e.key === 'Tab') {
          e.preventDefault()
          setMentionTrigger(null)
          return
        }
      }

      // Regular enter submission (no mention context)
      if (e.key === 'Enter' && !e.shiftKey && !mentionTrigger) {
        e.preventDefault()
        onSubmit?.()
        return
      }
    },
    [mentionTrigger, onSubmit]
  )

  // Handle focus
  const handleFocus = useCallback(() => {
    // Re-check for mention trigger on focus
    if (textareaRef.current) {
      const cursorIndex = textareaRef.current.selectionStart ?? 0
      const trigger = getMentionTrigger(value, cursorIndex)
      if (trigger) {
        setPickerPosition(getPickerPosition(textareaRef))
        setMentionTrigger(trigger)
      }
    }
  }, [value])

  // Add wrapper class when prefix is active (for Safari 15 / older Chrome compatibility)
  const wrapperClassName = `mc-mention-input-wrapper${!disabled ? ' mc-mention-input-wrapper--active' : ''}`

  return (
    <div ref={containerRef} className={`mc-mention-input-container ${className}`}>
      <div className={wrapperClassName}>
        {disabled ? (
          <div className="mc-mention-input-disabled-overlay" aria-label="Input disabled in viewer mode">
            <span className="mc-mention-input-label">HUMAN</span>
            <span className="mc-mention-input-readonly">View only — enter admin mode to post</span>
          </div>
        ) : (
          <span className="mc-mention-input-prefix" aria-hidden="true">HUMAN &gt;</span>
        )}
        <textarea
          ref={textareaRef}
          className="mc-mention-input"
          value={value}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onFocus={handleFocus}
          placeholder={placeholder}
          disabled={disabled}
          aria-label="Message input"
          rows={1}
        />
      </div>

      {/* Mention picker dropdown */}
      {mentionTrigger && !disabled && (
        <div
          className="mc-mention-picker-wrapper"
          style={{ top: pickerPosition.top, left: pickerPosition.left }}
        >
          <MentionPicker
            seats={activeSeats}
            patrons={patrons}
            filter={mentionTrigger.filter}
            onSelect={handleMentionSelect}
          />
        </div>
      )}
    </div>
  )
}

// =============================================================================
// Mention Error Component
// =============================================================================

export interface MentionErrorProps {
  /** Type of error */
  type: 'ambiguous' | 'unresolved'
  /** The handle that caused the error */
  handle: string
  /** Candidates for ambiguous mention */
  candidates?: PatronInfo[]
  /** Callback to disambiguate by selecting a candidate */
  onDisambiguate?: (candidate: PatronInfo) => void
  /** Callback to dismiss the error */
  onDismiss?: () => void
}

/**
 * Error display for mention resolution failures.
 *
 * - Ambiguous: Multiple patrons match the handle
 * - Unresolved: No patron matches the handle
 */
export function MentionError({
  type,
  handle,
  candidates = [],
  onDisambiguate,
  onDismiss,
}: MentionErrorProps) {
  return (
    <div className="mc-mention-error" role="alert">
      <div className="mc-mention-error-header">
        {type === 'ambiguous' ? (
          <>
            <span className="mc-mention-error-icon">⚠️</span>
            <span className="mc-mention-error-title">Ambiguous mention: @{handle}</span>
          </>
        ) : (
          <>
            <span className="mc-mention-error-icon">❓</span>
            <span className="mc-mention-error-title">Unknown mention: @{handle}</span>
          </>
        )}
        {onDismiss && (
          <button
            type="button"
            className="mc-mention-error-dismiss"
            onClick={onDismiss}
            aria-label="Dismiss"
          >
            ×
          </button>
        )}
      </div>

      {type === 'ambiguous' && candidates.length > 0 && (
        <div className="mc-mention-error-body">
          <p className="mc-mention-error-help">
            Multiple participants match this mention. Please select one:
          </p>
          <div className="mc-mention-error-candidates">
            {candidates.map((candidate) => (
              <button
                key={candidate.id}
                type="button"
                className="mc-mention-error-candidate"
                onClick={() => onDisambiguate?.(candidate)}
              >
                <span className="mc-mention-error-candidate-name">{candidate.name}</span>
                <span className="mc-mention-error-candidate-kind">{candidate.kind}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {type === 'unresolved' && (
        <div className="mc-mention-error-body">
          <p className="mc-mention-error-help">
            No participant found with this name. The mention will be saved as @{handle} but
            may not notify anyone.
          </p>
        </div>
      )}
    </div>
  )
}