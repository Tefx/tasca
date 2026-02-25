import { useState, useEffect, useCallback, type RefObject } from 'react'

interface UseKeyboardNavOptions {
  sayingsCount: number
  streamRef: RefObject<HTMLDivElement>
  /** Ref to any focusable element with a `.focus()` method (e.g. CommandConsoleRef). */
  inputRef: RefObject<{ focus: () => void } | null>
}

interface UseKeyboardNavResult {
  focusedIndex: number | null
  setFocusedIndex: (n: number | null) => void
}

/**
 * Keyboard navigation hook for the Stream saying list.
 *
 * Key bindings (only active when no input/textarea is focused):
 * - ArrowDown / j: Move focus to the next saying
 * - ArrowUp / k: Move focus to the previous saying
 * - g: Jump to the first saying
 * - G: Jump to the last saying
 * - /: Focus the command console input and clear focus index
 *
 * @param sayingsCount - Total number of sayings currently rendered
 * @param streamRef - Ref to the scrollable stream container
 * @param inputRef - Ref to the command console textarea
 *
 * @example
 * // Basic usage in a table view
 * const streamRef = useRef<HTMLDivElement>(null)
 * const consoleRef = useRef<CommandConsoleRef>(null)
 * const { focusedIndex, setFocusedIndex } = useKeyboardNav({
 *   sayingsCount: sayings.length,
 *   streamRef,
 *   inputRef: consoleRef,
 * })
 */
export function useKeyboardNav({
  sayingsCount,
  streamRef,
  inputRef,
}: UseKeyboardNavOptions): UseKeyboardNavResult {
  const [focusedIndex, setFocusedIndex] = useState<number | null>(null)

  const scrollToIndex = useCallback(
    (index: number) => {
      const stream = streamRef.current
      if (!stream) return
      const el = stream.querySelector<HTMLElement>(`[data-saying-index="${index}"]`)
      el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    },
    [streamRef]
  )

  const updateFocusedIndex = useCallback(
    (index: number | null) => {
      setFocusedIndex(index)
      if (index !== null) scrollToIndex(index)
    },
    [scrollToIndex]
  )

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Only activate when no input/textarea is focused
      const tag = (document.activeElement as HTMLElement)?.tagName?.toLowerCase()
      if (tag === 'input' || tag === 'textarea') return

      if (sayingsCount === 0) {
        if (e.key === '/') {
          e.preventDefault()
          inputRef.current?.focus()
        }
        return
      }

      switch (e.key) {
        case 'ArrowDown':
        case 'j': {
          e.preventDefault()
          setFocusedIndex((prev) => {
            const next = prev === null ? 0 : Math.min(prev + 1, sayingsCount - 1)
            scrollToIndex(next)
            return next
          })
          break
        }
        case 'ArrowUp':
        case 'k': {
          e.preventDefault()
          setFocusedIndex((prev) => {
            const next = prev === null ? sayingsCount - 1 : Math.max(prev - 1, 0)
            scrollToIndex(next)
            return next
          })
          break
        }
        case 'g': {
          e.preventDefault()
          updateFocusedIndex(0)
          break
        }
        case 'G': {
          e.preventDefault()
          updateFocusedIndex(sayingsCount - 1)
          break
        }
        case '/': {
          e.preventDefault()
          inputRef.current?.focus()
          // Clear saying highlight when transitioning from stream nav to console
          setFocusedIndex(null)
          break
        }
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [sayingsCount, inputRef, scrollToIndex, updateFocusedIndex])

  return { focusedIndex, setFocusedIndex }
}
