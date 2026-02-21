/**
 * SeatDeck component — Participant presence display for Mission Control.
 *
 * Shows participant cards with name, status badge, and presence indicator.
 * Supports human and agent participants with different visual treatment.
 *
 * Design source: docs/tasca-web-uiux-v0.1.md (Seat Deck spec)
 *
 * Features:
 * - Enhanced seat cards with status (active/idle/offline)
 * - Patron kind differentiation (agent/human visual treatment)
 * - Mention picker support via onSelect callback
 * - Current user highlighting
 */

import { useMemo } from 'react'
import type { Seat } from '../api/sayings'
import type { PatronKind } from '../api/patrons'

// Re-export Seat for consumers
export type { Seat } from '../api/sayings'

// =============================================================================
// Types
// =============================================================================

/** Extended seat info with patron details for UI rendering. */
export interface SeatWithPatron extends Seat {
  /** Patron details (may be undefined if patron lookup failed) */
  patron?: PatronInfo
}

/** Patron info for UI rendering (derived from Patron or Speaker). */
export interface PatronInfo {
  /** Unique identifier */
  id: string
  /** Display name */
  name: string
  /** Type of participant */
  kind: PatronKind
}

/** Seat presence status derived from heartbeat. */
export type SeatPresenceStatus = 'active' | 'idle' | 'offline'

interface SeatDeckProps {
  /** Seats (participants) to display */
  seats: Seat[]
  /** Number of currently active (non-expired) seats from the API */
  activeCount: number
  /** Optional: Patron data map (patron_id -> PatronInfo) */
  patrons?: Map<string, PatronInfo>
  /** Optional: Current user's patron ID (for highlight) */
  currentPatronId?: string
  /** Optional: Callback when a seat is selected (for mention picker integration) */
  onSelect?: (seat: SeatWithPatron) => void
  /** Optional: Filter to only show mentionable seats */
  mentionableOnly?: boolean
}

// =============================================================================
// Constants
// =============================================================================

/** TTL threshold for "active" presence (last heartbeat within this many seconds). */
const ACTIVE_THRESHOLD_SECONDS = 30

/** TTL threshold for "idle" presence (last heartbeat within this many seconds). */
const IDLE_THRESHOLD_SECONDS = 60

// =============================================================================
// Utility Functions
// =============================================================================

/**
 * Format an ISO date string to a compact relative time.
 */
function formatHeartbeat(iso: string): string {
  const date = new Date(iso)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffSeconds = Math.floor(diffMs / 1000)
  const diffMinutes = Math.floor(diffMs / 60_000)
  const diffHours = Math.floor(diffMs / 3_600_000)

  if (diffSeconds < 30) return 'just now'
  if (diffMinutes < 1) return `${diffSeconds}s ago`
  if (diffMinutes < 60) return `${diffMinutes}m ago`
  if (diffHours < 24) return `${diffHours}h ago`

  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

/**
 * Determine presence status based on last heartbeat.
 */
export function getPresenceStatus(lastHeartbeat: string): SeatPresenceStatus {
  const date = new Date(lastHeartbeat)
  const now = new Date()
  const diffSeconds = (now.getTime() - date.getTime()) / 1000

  if (diffSeconds <= ACTIVE_THRESHOLD_SECONDS) return 'active'
  if (diffSeconds <= IDLE_THRESHOLD_SECONDS) return 'idle'
  return 'offline'
}

/**
 * Get the initial(s) to display in the avatar circle.
 */
function avatarInitial(name: string, patronId: string): string {
  // Try to get initial from name, fallback to patron_id prefix
  if (name && name !== patronId) {
    return name.slice(0, 2).toUpperCase()
  }
  return patronId.slice(0, 2).toUpperCase()
}

/**
 * Human-readable label for presence status.
 */
function presenceLabel(status: SeatPresenceStatus): string {
  switch (status) {
    case 'active':
      return 'Active'
    case 'idle':
      return 'Idle'
    case 'offline':
      return 'Offline'
  }
}

/**
 * Uppercase badge text for presence status.
 */
function presenceBadgeText(status: SeatPresenceStatus): string {
  switch (status) {
    case 'active':
      return 'ONLINE'
    case 'idle':
      return 'IDLE'
    case 'offline':
      return 'OFFLINE'
  }
}

/**
 * CSS class suffix for presence status.
 */
function presenceClass(status: SeatPresenceStatus): string {
  return status
}

// =============================================================================
// Sub-Components
// =============================================================================

interface SeatCardProps {
  seat: Seat
  patron?: PatronInfo
  isCurrentUser: boolean
  onSelect?: (seat: SeatWithPatron) => void
  showPosition?: boolean
  position?: number
}

function SeatCard({ seat, patron, isCurrentUser, onSelect, showPosition, position }: SeatCardProps) {
  const presenceStatus = getPresenceStatus(seat.last_heartbeat)
  const displayName = patron?.name ?? seat.patron_id
  const patronKind = patron?.kind ?? 'agent'
  const isOffline = presenceStatus === 'offline'
  const isInteractive = !!onSelect && !isOffline

  const handleClick = () => {
    if (isInteractive && onSelect) {
      onSelect({
        ...seat,
        patron: patron ?? { id: seat.patron_id, name: seat.patron_id, kind: 'agent' },
      })
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (isInteractive && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault()
      handleClick()
    }
  }

  return (
    <div
      className={`mc-seat-card ${isCurrentUser ? 'mc-seat-card--current' : ''} ${isInteractive ? 'mc-seat-card--interactive' : ''} ${isOffline ? 'mc-seat-card--offline' : ''}`}
      role={isInteractive ? 'button' : undefined}
      tabIndex={isInteractive ? 0 : undefined}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      aria-label={`${displayName}, ${presenceLabel(presenceStatus)}, ${patronKind}`}
      aria-disabled={isOffline ? true : undefined}
    >
      {showPosition && position !== undefined && (
        <div className="mc-seat-position" aria-label={`Seat ${position}`}>
          {position}
        </div>
      )}
      <div
        className={`mc-seat-avatar mc-seat-avatar--${patronKind}`}
        aria-hidden="true"
      >
        {avatarInitial(displayName, seat.patron_id)}
      </div>
      <div className="mc-seat-info">
        <span className="mc-seat-name" title={displayName}>
          {displayName}
          {isCurrentUser && <span className="mc-seat-you-badge">(you)</span>}
        </span>
        <span className="mc-seat-meta">
          <span className={`mc-seat-presence mc-seat-presence--${presenceClass(presenceStatus)}`} />
          {formatHeartbeat(seat.last_heartbeat)}
        </span>
      </div>
      <div className="mc-seat-badges">
        <span
          className={`mc-seat-presence-badge mc-seat-presence-badge--${presenceClass(presenceStatus)}`}
          aria-label={`Status: ${presenceLabel(presenceStatus)}`}
        >
          <span className="mc-seat-presence-badge-dot" />
          {presenceBadgeText(presenceStatus)}
        </span>
        <span className={`mc-seat-kind mc-seat-kind--${patronKind}`}>
          {patronKind.toUpperCase()}
        </span>
        {seat.state === 'left' && (
          <span className="mc-seat-state mc-seat-state--left" aria-label="Left">
            LEFT
          </span>
        )}
      </div>
    </div>
  )
}

// =============================================================================
// Main Component
// =============================================================================

export function SeatDeck({
  seats,
  activeCount,
  patrons,
  currentPatronId,
  onSelect,
  mentionableOnly = false,
}: SeatDeckProps) {
  // Derive patron info from seats
  const seatsWithPatrons = useMemo<SeatWithPatron[]>(() => {
    return seats.map((seat) => ({
      ...seat,
      patron: patrons?.get(seat.patron_id),
    }))
  }, [seats, patrons])

  // Filter seats for mentionable-only mode
  const displaySeats = useMemo(() => {
    if (!mentionableOnly) return seatsWithPatrons
    // Only show seats that are active and could potentially be mentioned
    return seatsWithPatrons.filter((seat) => {
      const presenceStatus = getPresenceStatus(seat.last_heartbeat)
      return seat.state === 'joined' && presenceStatus !== 'offline'
    })
  }, [seatsWithPatrons, mentionableOnly])

  // Count by presence status
  const counts = useMemo(() => {
    let active = 0
    let idle = 0
    let offline = 0

    for (const seat of seatsWithPatrons) {
      if (seat.state !== 'joined') continue
      const status = getPresenceStatus(seat.last_heartbeat)
      if (status === 'active') active++
      else if (status === 'idle') idle++
      else offline++
    }

    return { active, idle, offline }
  }, [seatsWithPatrons])

  return (
    <div className="mc-col-right">
      <div
        className={`mc-seat-deck ${mentionableOnly ? 'mc-seat-deck--picker' : ''}`}
        role={mentionableOnly ? 'listbox' : 'complementary'}
        aria-label={mentionableOnly ? 'Select participant to mention' : 'Participants'}
      >
        <div className="mc-seat-deck-header">
          {mentionableOnly ? 'Select participant' : 'Seats'}
          {!mentionableOnly && (
            <span className="mc-seat-deck-count" aria-label={`${activeCount} active`}>
              {activeCount} active
            </span>
          )}
        </div>

        {!mentionableOnly && (
          <div className="mc-seat-deck-stats">
            <span className="mc-seat-deck-stat mc-seat-deck-stat--active">
              <span className="mc-seat-deck-stat-dot" />
              {counts.active} active
            </span>
            <span className="mc-seat-deck-stat mc-seat-deck-stat--idle">
              <span className="mc-seat-deck-stat-dot" />
              {counts.idle} idle
            </span>
            <span className="mc-seat-deck-stat mc-seat-deck-stat--offline">
              <span className="mc-seat-deck-stat-dot" />
              {counts.offline} offline
            </span>
          </div>
        )}

        {displaySeats.length === 0 ? (
          <div className="mc-seat-deck-empty" role="status">
            {mentionableOnly ? 'No active participants to mention' : 'No participants yet'}
          </div>
        ) : (
          <div role={mentionableOnly ? 'presentation' : 'list'} aria-label="Participant list">
            {displaySeats.map((seat, index) => (
              <SeatCard
                key={seat.id}
                seat={seat}
                patron={seat.patron}
                isCurrentUser={currentPatronId === seat.patron_id}
                onSelect={onSelect}
                showPosition={!mentionableOnly}
                position={index + 1}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// =============================================================================
// Standalone MentionPicker Component
// =============================================================================

export interface MentionPickerProps {
  /** Seats to display in the picker */
  seats: Seat[]
  /** Optional patron data map */
  patrons?: Map<string, PatronInfo>
  /** Filter string (fuzzy search) */
  filter?: string
  /** Callback when a participant is selected */
  onSelect: (participant: { patronId: string; displayName: string }) => void
  /** Callback when picker is closed/cancelled */
  onClose?: () => void
}

/**
 * Mention picker dropdown for @mentions.
 *
 * Shows filtered list of participants that can be mentioned.
 * Supports fuzzy search filtering by name or patron ID.
 */
export function MentionPicker({
  seats,
  patrons,
  filter = '',
  onSelect,
}: MentionPickerProps) {
  // Filter and sort participants
  const filteredSeats = useMemo(() => {
    const activeSeats = seats.filter((seat) => {
      // Only show joined and non-offline seats
      if (seat.state !== 'joined') return false
      const presenceStatus = getPresenceStatus(seat.last_heartbeat)
      if (presenceStatus === 'offline') return false
      return true
    })

    if (!filter.trim()) return activeSeats

    const filterLower = filter.toLowerCase()
    return activeSeats.filter((seat) => {
      const patron = patrons?.get(seat.patron_id)
      const name = patron?.name ?? seat.patron_id
      const nameLower = name.toLowerCase()
      const patronIdLower = seat.patron_id.toLowerCase()

      // Fuzzy-ish match: contains filter in name or ID
      return nameLower.includes(filterLower) || patronIdLower.includes(filterLower)
    })
  }, [seats, patrons, filter])

  const handleSelect = (seat: Seat) => {
    const patron = patrons?.get(seat.patron_id)
    onSelect({
      patronId: seat.patron_id,
      displayName: patron?.name ?? seat.patron_id,
    })
  }

  if (filteredSeats.length === 0) {
    return (
      <div className="mc-mention-picker mc-mention-picker--empty" role="listbox">
        <div className="mc-mention-picker-empty">
          {filter.trim() ? (
            <>No participants match "{filter}"</>
          ) : (
            'No active participants'
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="mc-mention-picker" role="listbox" aria-label="Mention a participant">
      {filteredSeats.map((seat) => {
        const patron = patrons?.get(seat.patron_id)
        const displayName = patron?.name ?? seat.patron_id
        const presenceStatus = getPresenceStatus(seat.last_heartbeat)
        const patronKind = patron?.kind ?? 'agent'

        return (
          <button
            key={seat.id}
            type="button"
            className="mc-mention-item"
            role="option"
            onClick={() => handleSelect(seat)}
            aria-selected={false}
          >
            <div className={`mc-mention-avatar mc-mention-avatar--${patronKind}`}>
              {avatarInitial(displayName, seat.patron_id)}
            </div>
            <div className="mc-mention-info">
              <span className="mc-mention-name">{displayName}</span>
              <span className="mc-mention-meta">
                <span className={`mc-mention-presence mc-mention-presence--${presenceStatus}`} />
                {patronKind}
              </span>
            </div>
          </button>
        )
      })}
    </div>
  )
}