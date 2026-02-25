import { useState, useEffect } from 'react'

/**
 * Returns a Date that auto-refreshes every `intervalMs` milliseconds.
 * Used to keep relative timestamps (e.g., '5m ago') accurate without
 * requiring new data from the server.
 *
 * @param intervalMs - Refresh interval in milliseconds (default: 30000 = 30s)
 *
 * @example
 * ```tsx
 * function TimeDisplay() {
 *   const now = useNow() // Refreshes every 30 seconds
 *   return <span>Last checked: {now.toLocaleTimeString()}</span>
 * }
 *
 * // With custom interval (5 seconds)
 * const now = useNow(5000)
 * ```
 */
export function useNow(intervalMs: number = 30_000): Date {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])

  return now
}
