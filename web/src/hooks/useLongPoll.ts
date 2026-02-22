/// <reference types="vite/client" />
/**
 * useSayingsStream — Long-poll hook for real-time sayings updates.
 *
 * Replaces the naive setInterval approach with a proper recursive async loop
 * that uses AbortController for clean teardown and tracks sequence via a ref
 * to prevent stale-closure / re-render loops.
 *
 * Protocol (design source: docs/tasca-http-api-v0.1.md):
 *   1. Initial load: GET /sayings — fetch all existing sayings + next_sequence
 *   2. Loop: GET /sayings/wait?since_sequence=N&wait_ms=10000&include_table=true
 *   3. On success (even empty): append new sayings, update sequence, loop immediately
 *   4. On network error: exponential backoff 1s→2s→4s→8s→16s→30s (cap), then resume
 *   5. On AbortError: exit cleanly (component unmounted)
 *
 * Backoff thresholds: 3+ consecutive errors → connectionStatus='offline'.
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import { listSayings, waitForSayings, type Saying } from '../api/sayings'
import type { Table as TableType } from '../api/tables'

// =============================================================================
// Types
// =============================================================================

/** Connection status for the live stream badge. */
export type ConnectionStatus = 'live' | 'connecting' | 'offline'

/** Return value of useSayingsStream. */
export interface SayingsStreamResult {
  /** All sayings accumulated since mount, ordered by sequence ascending. */
  sayings: Saying[]
  /** Latest table state — updated when include_table returns a new snapshot. */
  table: TableType | null
  /** Next sequence number (informational; stored in ref internally). */
  nextSequence: number
  /** Live/connecting/offline status for the stream badge. */
  connectionStatus: ConnectionStatus
  /** Optimistically append a saying (e.g. immediately after local post). Deduped against existing. */
  appendSaying: (saying: Saying) => void
}

// =============================================================================
// Constants
// =============================================================================

/** Backoff delay schedule in milliseconds. */
const BACKOFF_DELAYS_MS = [1_000, 2_000, 4_000, 8_000, 16_000, 30_000]

/** Number of consecutive errors before status transitions to 'offline'. */
const OFFLINE_THRESHOLD = 3

/**
 * Granularity of each backoff sleep slice.
 * The total backoff delay is split into slices so we can abort early
 * when the component unmounts mid-sleep.
 */
const BACKOFF_SLICE_MS = 500

// =============================================================================
// Helpers
// =============================================================================

/**
 * Sleep for `totalMs`, but in slices of BACKOFF_SLICE_MS so the caller
 * can be aborted quickly. Returns early if signal fires mid-sleep.
 */
async function slicedDelay(totalMs: number, signal: AbortSignal): Promise<void> {
  let remaining = totalMs
  while (remaining > 0 && !signal.aborted) {
    const slice = Math.min(remaining, BACKOFF_SLICE_MS)
    await new Promise<void>((resolve) => {
      const t = setTimeout(resolve, slice)
      signal.addEventListener('abort', () => { clearTimeout(t); resolve() }, { once: true })
    })
    remaining -= slice
  }
}

// =============================================================================
// Hook
// =============================================================================

/**
 * Subscribe to a real-time sayings stream for a table.
 *
 * Starts with a full GET /sayings load, then enters a long-poll loop via
 * GET /sayings/wait. Cleans up in-flight requests on unmount via AbortController.
 */
export function useSayingsStream(
  tableId: string | undefined
): SayingsStreamResult {
  const [sayings, setSayings] = useState<Saying[]>([])
  const [table, setTable] = useState<TableType | null>(null)
  const [nextSequence, setNextSequence] = useState(0)
  const [connectionStatus, setConnectionStatus] =
    useState<ConnectionStatus>('connecting')

  // Stable refs — avoid stale closure issues in the async loop.
  const nextSequenceRef = useRef(0)
  const consecutiveErrorsRef = useRef(0)

  // Expose the latest nextSequence to state for consumers, but drive
  // the loop from the ref to avoid re-triggering the effect.
  const updateNextSequence = useCallback((seq: number) => {
    nextSequenceRef.current = seq
    setNextSequence(seq)
  }, [])

  useEffect(() => {
    if (!tableId) return

    const controller = new AbortController()
    const { signal } = controller

    async function loop(): Promise<void> {
      // -----------------------------------------------------------------------
      // Phase 1: Initial load — GET /sayings
      // -----------------------------------------------------------------------
      try {
        const initial = await listSayings(tableId as string)

        if (signal.aborted) return

        setSayings(initial.sayings)
        updateNextSequence(initial.next_sequence)
        consecutiveErrorsRef.current = 0
        setConnectionStatus('live')

        if (import.meta.env.DEV) {
          console.log(
            '[stream] initial load:',
            initial.sayings.length,
            'sayings, next_sequence:',
            initial.next_sequence
          )
        }
      } catch (err) {
        if (signal.aborted) return
        handleError(err, signal, loop)
        return
      }

      // -----------------------------------------------------------------------
      // Phase 2: Long-poll loop — GET /sayings/wait
      // -----------------------------------------------------------------------
      while (!signal.aborted) {
        try {
          const response = await waitForSayings(
            tableId as string,
            nextSequenceRef.current,
            signal,
            10_000
          )

          if (signal.aborted) return

          const newSayings = response.sayings
          const nextSeq = response.next_sequence

          // Append only genuinely new sayings (guard against server duplication).
          if (newSayings.length > 0) {
            setSayings((prev) => {
              const existingIds = new Set(prev.map((s) => s.id))
              const deduped = newSayings.filter((s) => !existingIds.has(s.id))
              return deduped.length > 0 ? [...prev, ...deduped] : prev
            })
          }

          updateNextSequence(nextSeq)

          // Update table state if the server returned a fresh snapshot.
          if (response.table) {
            setTable(response.table)
          }

          consecutiveErrorsRef.current = 0
          setConnectionStatus('live')

          if (import.meta.env.DEV) {
            console.log(
              '[stream] new sayings:',
              newSayings.length,
              'next_sequence:',
              nextSeq
            )
          }
        } catch (err) {
          if (signal.aborted) return

          consecutiveErrorsRef.current += 1
          const errorCount = consecutiveErrorsRef.current

          // Transition status based on error count.
          if (errorCount >= OFFLINE_THRESHOLD) {
            setConnectionStatus('offline')
          } else {
            setConnectionStatus('connecting')
          }

          // Exponential backoff — cap at the last entry in the schedule.
          const backoffIndex = Math.min(
            errorCount - 1,
            BACKOFF_DELAYS_MS.length - 1
          )
          const delayMs = BACKOFF_DELAYS_MS[backoffIndex]

          if (import.meta.env.DEV) {
            console.log('[stream] backoff:', delayMs, 'ms (error #', errorCount, ')')
          }

          // Wait before retrying, in small slices so abort is responsive.
          await slicedDelay(delayMs, signal)
        }
      }
    }

    /**
     * Handle an error during Phase 1 initial load with backoff + retry.
     * Uses the same backoff schedule as the poll loop.
     */
    async function handleError(
      _err: unknown,
      abortSignal: AbortSignal,
      retry: () => Promise<void>
    ): Promise<void> {
      consecutiveErrorsRef.current += 1
      const errorCount = consecutiveErrorsRef.current

      if (errorCount >= OFFLINE_THRESHOLD) {
        setConnectionStatus('offline')
      } else {
        setConnectionStatus('connecting')
      }

      const backoffIndex = Math.min(
        errorCount - 1,
        BACKOFF_DELAYS_MS.length - 1
      )
      const delayMs = BACKOFF_DELAYS_MS[backoffIndex]

      if (import.meta.env.DEV) {
        console.log('[stream] initial load backoff:', delayMs, 'ms')
      }

      await slicedDelay(delayMs, abortSignal)

      if (!abortSignal.aborted) {
        await retry()
      }
    }

    // Reset state on tableId change before starting the loop.
    setSayings([])
    setTable(null)
    updateNextSequence(0)
    consecutiveErrorsRef.current = 0
    setConnectionStatus('connecting')

    loop()

    return () => {
      controller.abort()
    }
  }, [tableId, updateNextSequence])

  const appendSaying = useCallback((saying: Saying) => {
    setSayings((prev) => {
      if (prev.some((s) => s.id === saying.id)) return prev
      return [...prev, saying]
    })
  }, [])

  return { sayings, table, nextSequence, connectionStatus, appendSaying }
}
