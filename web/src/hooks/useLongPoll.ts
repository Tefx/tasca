/**
 * Long polling hook for real-time updates.
 *
 * Provides a hook for polling API endpoints at regular intervals.
 */

import { useEffect, useCallback } from 'react'

interface UseLongPollOptions {
  interval?: number
  enabled?: boolean
}

export function useLongPoll(
  callback: () => Promise<void>,
  options: UseLongPollOptions = {}
) {
  const { interval = 5000, enabled = true } = options

  const poll = useCallback(async () => {
    if (enabled) {
      await callback()
    }
  }, [callback, enabled])

  useEffect(() => {
    if (!enabled) return

    // Initial call
    poll()

    // Set up interval
    const intervalId = setInterval(poll, interval)

    return () => clearInterval(intervalId)
  }, [poll, interval, enabled])
}