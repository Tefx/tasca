/**
 * Stream component — Saying log display for Mission Control.
 *
 * Renders sayings as full-width log blocks (NOT chat bubbles).
 * Agent sayings get a tinted background + monospace content.
 * Human sayings get a high-contrast left border.
 *
 * Design source: docs/tasca-web-uiux-v0.1.md (Stream spec)
 *
 * New in web.long_poll_stream:
 * - connectionStatus prop drives a live/connecting/offline badge.
 * - Smart auto-scroll: follows tail when user is at bottom; freezes and
 *   shows a floating "N new sayings" button when the user scrolls up.
 * - New sayings highlight briefly with a CSS animation on entry.
 */

import { useEffect, useRef, useState, useCallback, type RefObject } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Saying, SpeakerKind } from '../api/sayings'
import type { ConnectionStatus } from '../hooks/useLongPoll'
import type { TableStatus } from '../api/tables'
import { useNow } from '../hooks/useNow'

// =============================================================================
// Accessibility: Debounced Aria-Live Announcements
// =============================================================================

/**
 * Debounce delay for aria-live announcements (ms).
 * Prevents announcement spam during rapid saying bursts.
 * Source: WCAG 2.1 + screen reader best practices.
 */
const ANNOUNCEMENT_DEBOUNCE_MS = 2000

/**
 * Keep live-region text stable long enough for DOM/assertion observability.
 * Source: ux_a11y.a11y-live-region-fix blocker report.
 */
const ANNOUNCEMENT_MIN_VISIBLE_MS = 4000

/**
 * Hook to debounce aria-live announcements based on count changes.
 * Returns announcement text after a settle period.
 *
 * Rationale: Screen readers announce every aria-live change.
 * Without debouncing, a burst of 10 sayings triggers 10 rapid
 * "X sayings in stream" announcements. This hook delays announcement
 * until the count stabilizes, then announces the final count.
 *
 * @param count - Current saying count
 * @returns Debounced announcement text (empty string when nothing to announce)
 */
function useDebouncedAnnouncement(count: number): string {
  const [announcement, setAnnouncement] = useState('')
  const lastAnnouncedCountRef = useRef(count)
  const pendingCountRef = useRef(count)
  const previousCountRef = useRef(count)
  const debounceTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const clearTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    // Clear pending timeout on cleanup
    return () => {
      if (debounceTimeoutRef.current) {
        clearTimeout(debounceTimeoutRef.current)
      }
      if (clearTimeoutRef.current) {
        clearTimeout(clearTimeoutRef.current)
      }
    }
  }, [])

  useEffect(() => {
    // Reset tracking if stream empties.
    if (count === 0) {
      if (debounceTimeoutRef.current) {
        clearTimeout(debounceTimeoutRef.current)
        debounceTimeoutRef.current = null
      }
      if (clearTimeoutRef.current) {
        clearTimeout(clearTimeoutRef.current)
        clearTimeoutRef.current = null
      }
      setAnnouncement('')
      lastAnnouncedCountRef.current = 0
      pendingCountRef.current = 0
      previousCountRef.current = 0
      return
    }

    const previousCount = previousCountRef.current
    previousCountRef.current = count

    // Rebaseline after decreases so rebound increases are not suppressed.
    if (count < previousCount) {
      if (debounceTimeoutRef.current) {
        clearTimeout(debounceTimeoutRef.current)
        debounceTimeoutRef.current = null
      }
      if (clearTimeoutRef.current) {
        clearTimeout(clearTimeoutRef.current)
        clearTimeoutRef.current = null
      }
      setAnnouncement('')
      lastAnnouncedCountRef.current = count
      pendingCountRef.current = count
      return
    }

    // Only announce when count increases beyond the current baseline.
    if (count <= lastAnnouncedCountRef.current) {
      pendingCountRef.current = count
      return
    }

    pendingCountRef.current = count

    // Clear any pending debounce from a previous burst.
    if (debounceTimeoutRef.current) {
      clearTimeout(debounceTimeoutRef.current)
    }

    // Debounce: wait after arrivals settle, then announce aggregate delta.
    debounceTimeoutRef.current = setTimeout(() => {
      const latestCount = pendingCountRef.current
      const previousCount = lastAnnouncedCountRef.current
      const newSayings = latestCount - previousCount

      if (newSayings <= 0) {
        debounceTimeoutRef.current = null
        return
      }

      const text =
        newSayings === 1
          ? '1 new saying in stream'
          : `${newSayings} new sayings in stream`
      setAnnouncement(text)

      if (clearTimeoutRef.current) {
        clearTimeout(clearTimeoutRef.current)
      }
      clearTimeoutRef.current = setTimeout(() => {
        setAnnouncement('')
        clearTimeoutRef.current = null
      }, ANNOUNCEMENT_MIN_VISIBLE_MS)

      lastAnnouncedCountRef.current = latestCount
      debounceTimeoutRef.current = null
    }, ANNOUNCEMENT_DEBOUNCE_MS)
  }, [count])

  return announcement
}

// =============================================================================
// Types
// =============================================================================

interface StreamProps {
  /** Sayings to display, ordered by sequence ascending. */
  sayings: Saying[]
  /**
   * Current connection status for the stream badge.
   * Design source: task spec web.long_poll_stream — Connection status indicator.
   */
  connectionStatus: ConnectionStatus
  /** Optional table status — drives contextual empty state messages. */
  tableStatus?: TableStatus
  /** Index of the currently keyboard-focused saying, or null. */
  focusedIndex?: number | null
  /** External ref for the scrollable container (shared with keyboard nav hook). */
  containerRef?: RefObject<HTMLDivElement>
}

// =============================================================================
// Constants
// =============================================================================

/**
 * How close to the bottom (in pixels) the user must be for auto-scroll
 * to be considered "at bottom". Accounts for sub-pixel scroll positions.
 */
const AT_BOTTOM_THRESHOLD_PX = 48

// =============================================================================
// Stream Scroll Hook
// =============================================================================

interface UseStreamScrollResult {
  /** Whether user is at/near the bottom of the stream. */
  isAtBottom: boolean
  /** Count of unread sayings while scrolled up. */
  unreadCount: number
  /** Index threshold for "new" saying highlight. */
  initialCount: number
  /** Scroll event handler. */
  handleScroll: () => void
  /** Jump to bottom action. */
  jumpToBottom: () => void
}

/**
 * Hook to manage stream scroll behavior: auto-scroll, unread tracking, and jump-to-bottom.
 * Encapsulates all scroll-related state and effects for the Stream component.
 */
function useStreamScroll(
  streamRef: RefObject<HTMLDivElement>,
  sayingsLength: number
): UseStreamScrollResult {
  const initialCountRef = useRef(0)
  const [isAtBottom, setIsAtBottom] = useState(true)
  const [unreadCount, setUnreadCount] = useState(0)
  const countAtScrollUpRef = useRef(0)

  // On mount with data, mark all existing sayings as "seen"
  if (initialCountRef.current === 0 && sayingsLength > 0) {
    initialCountRef.current = sayingsLength
  }

  const handleScroll = useCallback(() => {
    const el = streamRef.current
    if (!el) return

    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
    const atBottom = distanceFromBottom <= AT_BOTTOM_THRESHOLD_PX
    setIsAtBottom(atBottom)

    if (atBottom) {
      setUnreadCount(0)
      countAtScrollUpRef.current = 0
      initialCountRef.current = sayingsLength
    }
  }, [streamRef, sayingsLength])

  // Auto-scroll when new sayings arrive
  useEffect(() => {
    const el = streamRef.current
    if (!el || sayingsLength === 0) return

    if (isAtBottom) {
      el.scrollTop = el.scrollHeight
      initialCountRef.current = sayingsLength
    } else {
      const newSinceScrollUp = sayingsLength - countAtScrollUpRef.current
      if (countAtScrollUpRef.current === 0) {
        countAtScrollUpRef.current = sayingsLength
      } else if (newSinceScrollUp > 0) {
        setUnreadCount(newSinceScrollUp)
      }
    }
  }, [streamRef, sayingsLength, isAtBottom])

  const jumpToBottom = useCallback(() => {
    const el = streamRef.current
    if (!el) return
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    el.scrollTo({ top: el.scrollHeight, behavior: prefersReducedMotion ? 'auto' : 'smooth' })
    setIsAtBottom(true)
    setUnreadCount(0)
    countAtScrollUpRef.current = 0
    initialCountRef.current = sayingsLength
  }, [streamRef, sayingsLength])

  return {
    isAtBottom,
    unreadCount,
    initialCount: initialCountRef.current,
    handleScroll,
    jumpToBottom,
  }
}

// =============================================================================
// Utility
// =============================================================================

/** Format an ISO date string to a compact time display. */
function formatTime(iso: string, now: Date): string {
  const date = new Date(iso)
  const diffMs = now.getTime() - date.getTime()
  const diffMinutes = Math.floor(diffMs / 60_000)
  const diffHours = Math.floor(diffMs / 3_600_000)

  if (diffMinutes < 1) return 'just now'
  if (diffMinutes < 60) return `${diffMinutes}m ago`
  if (diffHours < 24) return `${diffHours}h ago`

  return date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    month: 'short',
    day: 'numeric',
  })
}

/** Get the CSS class suffix for a speaker kind. */
function speakerKindClass(kind: SpeakerKind): string {
  return kind
}

// =============================================================================
// Connection Badge
// =============================================================================

interface ConnectionBadgeProps {
  status: ConnectionStatus
}

/** Small indicator dot with label for stream connection state. */
function ConnectionBadge({ status }: ConnectionBadgeProps) {
  const label =
    status === 'live'
      ? 'live'
      : status === 'connecting'
        ? 'connecting'
        : 'offline'

  return (
    <span
      className={`mc-stream-conn mc-stream-conn--${status}`}
      aria-label={`Stream status: ${label}`}
      title={`Stream ${label}`}
    >
      <span className="mc-stream-conn-dot" aria-hidden="true" />
      {label}
    </span>
  )
}

// =============================================================================
// Sub-Components
// =============================================================================

interface LogBlockProps {
  saying: Saying
  /** Whether this block was added in the current mount cycle (triggers highlight). */
  isNew: boolean
  /** Current time — passed from parent to keep timestamps fresh. */
  now: Date
  /** Index for keyboard navigation (data-saying-index attribute). */
  sayingIndex: number
  /** Whether this block is keyboard-focused. */
  isFocused?: boolean
}

function LogBlock({ saying, isNew, now, sayingIndex, isFocused }: LogBlockProps) {
  const kind = saying.speaker.kind
  const isMonospace = kind === 'agent' || kind === 'patron'

  return (
    <article
      className={`mc-log-block mc-log-block--${speakerKindClass(kind)}${isNew ? ' mc-log-block--new' : ''}${isFocused ? ' mc-log-block--focused' : ''}`}
      data-saying-index={sayingIndex}
      aria-label={`Saying ${saying.sequence} by ${saying.speaker.name} (${kind})`}
    >
      {/* Header: sequence, speaker name, pin marker, timestamp */}
      <div className="mc-log-header">
        <span className="mc-log-seq" aria-label="Sequence number">
          #{saying.sequence}
        </span>
        {saying.reply_to != null && (
          <span className="mc-log-reply-anchor" title={`Reply to #${saying.reply_to}`}>
            ↩ #{saying.reply_to}
          </span>
        )}
        <span className="mc-log-speaker">{saying.speaker.name}</span>
        {saying.pinned && (
          <span className="mc-log-pin" aria-label="Pinned" title="Pinned">
            📌
          </span>
        )}
        <time className="mc-log-time" dateTime={saying.created_at}>
          {formatTime(saying.created_at, now)}
        </time>
      </div>

      {/* Content — Markdown rendered */}
      <div
        className={`mc-log-content${isMonospace ? ' mc-log-content--mono' : ''}`}
      >
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            // Keep links safe — open in new tab
            a: ({ ...props }) => (
              <a {...props} target="_blank" rel="noopener noreferrer" />
            ),
          }}
        >
          {saying.content}
        </ReactMarkdown>
      </div>

      {/* Unresolved mention chips */}
      {saying.mentions_unresolved && saying.mentions_unresolved.length > 0 && (
        <div className="mc-log-mentions-warn" role="alert" aria-label="Unresolved mentions">
          <span className="mc-log-mentions-warn-icon" aria-hidden="true">⚠</span>
          {saying.mentions_unresolved.map((handle) => (
            <span key={handle} className="mc-log-mention-chip mc-log-mention-chip--unresolved">
              @{handle}
            </span>
          ))}
        </div>
      )}
    </article>
  )
}

// =============================================================================
// Main Component
// =============================================================================

/**
 * Stream component — Saying log display for Mission Control.
 *
 * @example
 * // Basic usage
 * <Stream
 *   sayings={sayingsArray}
 *   connectionStatus="live"
 *   tableStatus="open"
 * />
 *
 * @example
 * // With keyboard nav focus
 * <Stream
 *   sayings={sayingsArray}
 *   connectionStatus="live"
 *   tableStatus="open"
 *   focusedIndex={2}
 *   containerRef={streamRef}
 * />
 */
export function Stream({ sayings, connectionStatus, tableStatus, focusedIndex, containerRef }: StreamProps) {
  const now = useNow()
  const internalRef = useRef<HTMLDivElement>(null)
  const streamRef = containerRef ?? internalRef

  const announcement = useDebouncedAnnouncement(sayings.length)
  const { isAtBottom, unreadCount, initialCount, handleScroll, jumpToBottom } =
    useStreamScroll(streamRef, sayings.length)

  return (
    <div className="mc-stream-panel">
      <div className="mc-stream-header">
        <span>
          Stream ({sayings.length}{' '}
          {sayings.length === 1 ? 'saying' : 'sayings'})
        </span>
        <ConnectionBadge status={connectionStatus} />
      </div>

      <div className="mc-stream-body">
        {/*
          Visually hidden live region for screen reader announcements.
          Uses aria-live="polite" to announce count-based updates after
          a 2-second debounce period. This replaces the aria-live on the
          scroll container which caused per-message announcement spam.

          Accessibility note: sr-only class makes this invisible to sighted
          users while remaining accessible to screen readers.
        */}
        <div className="sr-only" aria-live="polite" aria-atomic="true" data-testid="stream-live-region">
          {announcement}
        </div>

        {/*
          Persistent log container with role="log" for screen reader navigation.
          Must remain in DOM even when empty to maintain consistent a11y semantics.
          Empty-state message is rendered as child content when no sayings exist.
        */}
        <div
          ref={streamRef}
          className="mc-stream"
          role="log"
          aria-label="Discussion stream"
          onScroll={handleScroll}
        >
          {sayings.length === 0 ? (
            <div className="mc-stream-empty" role="status">
              {tableStatus === 'paused' ? (
                <p>This table is paused. Resume the table to allow new sayings.</p>
              ) : tableStatus === 'closed' ? (
                <p>This discussion has ended. No sayings were recorded.</p>
              ) : (
                <p>Waiting for the conversation to begin. Sayings will appear here in real time.</p>
              )}
            </div>
          ) : (
            sayings.map((saying, index) => (
              <LogBlock
                key={saying.id}
                saying={saying}
                isNew={index >= initialCount}
                now={now}
                sayingIndex={index}
                isFocused={focusedIndex === index}
              />
            ))
          )}
        </div>

        {/* Floating "N new sayings" button — visible only when scrolled up and there are unread sayings */}
        {!isAtBottom && unreadCount > 0 && (
          <button
            type="button"
            className="mc-stream-jump"
            onClick={jumpToBottom}
            aria-label={`Jump to bottom — ${unreadCount} new ${unreadCount === 1 ? 'saying' : 'sayings'}`}
          >
            &darr; {unreadCount} new {unreadCount === 1 ? 'saying' : 'sayings'}
          </button>
        )}
      </div>
    </div>
  )
}
