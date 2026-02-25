import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useKeyboardNav } from './useKeyboardNav'

/**
 * Tests for useKeyboardNav hook.
 *
 * Covers:
 * - ArrowUp/ArrowDown focus navigation (SG-3 precondition)
 * - '/' key clears focus and focuses composer
 * - j/k keys (Vim-style) still work
 * - g/G keys for first/last navigation
 * - No interference when input/textarea is focused
 */

function createMockRefs() {
  const mockFocus = vi.fn()
  const mockScrollIntoView = vi.fn()

  const inputRef = { current: { focus: mockFocus } }
  const streamRef = {
    current: {
      querySelector: vi.fn().mockReturnValue({
        scrollIntoView: mockScrollIntoView,
      }),
    } as unknown as HTMLDivElement,
  }

  return { inputRef, streamRef, mockFocus, mockScrollIntoView }
}

function fireKeyDown(key: string, shiftKey = false) {
  const event = new KeyboardEvent('keydown', { key, shiftKey, bubbles: true })
  document.dispatchEvent(event)
  return event
}

describe('useKeyboardNav', () => {
  let { inputRef, streamRef, mockFocus, mockScrollIntoView } = createMockRefs()

  beforeEach(() => {
    vi.useFakeTimers()
    const refs = createMockRefs()
    inputRef = refs.inputRef
    streamRef = refs.streamRef
    mockFocus = refs.mockFocus
    mockScrollIntoView = refs.mockScrollIntoView
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  describe('ArrowUp/ArrowDown focus navigation', () => {
    it('ArrowDown creates focused state on first saying when no focus exists', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 5,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      expect(result.current.focusedIndex).toBe(null)

      act(() => {
        fireKeyDown('ArrowDown')
      })

      expect(result.current.focusedIndex).toBe(0)
      expect(mockScrollIntoView).toHaveBeenCalled()
    })

    it('ArrowDown moves focus to next saying', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 5,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      // First ArrowDown creates focus at index 0
      act(() => {
        fireKeyDown('ArrowDown')
      })
      expect(result.current.focusedIndex).toBe(0)

      // Second ArrowDown moves to index 1
      act(() => {
        fireKeyDown('ArrowDown')
      })
      expect(result.current.focusedIndex).toBe(1)
    })

    it('ArrowDown does not exceed max index', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 3,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      // Move to last saying (index 2)
      act(() => {
        fireKeyDown('ArrowDown')
        fireKeyDown('ArrowDown')
        fireKeyDown('ArrowDown')
      })
      expect(result.current.focusedIndex).toBe(2)

      // One more ArrowDown should stay at 2
      act(() => {
        fireKeyDown('ArrowDown')
      })
      expect(result.current.focusedIndex).toBe(2)
    })

    it('ArrowUp creates focused state on last saying when no focus exists', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 5,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      expect(result.current.focusedIndex).toBe(null)

      act(() => {
        fireKeyDown('ArrowUp')
      })

      expect(result.current.focusedIndex).toBe(4) // Last saying
      expect(mockScrollIntoView).toHaveBeenCalled()
    })

    it('ArrowUp moves focus to previous saying', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 5,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      // Start at last saying
      act(() => {
        fireKeyDown('ArrowUp')
      })
      expect(result.current.focusedIndex).toBe(4)

      // ArrowUp moves to index 3
      act(() => {
        fireKeyDown('ArrowUp')
      })
      expect(result.current.focusedIndex).toBe(3)
    })

    it('ArrowUp does not go below index 0', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 3,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      // Move to first saying (index 0)
      act(() => {
        fireKeyDown('ArrowDown')
      })
      expect(result.current.focusedIndex).toBe(0)

      // ArrowUp should stay at 0
      act(() => {
        fireKeyDown('ArrowUp')
      })
      expect(result.current.focusedIndex).toBe(0)
    })
  })

  describe('/ key clears focus and focuses composer', () => {
    it('slash key focuses input and clears focus index', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 5,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      // Create focus state
      act(() => {
        fireKeyDown('ArrowDown')
        fireKeyDown('ArrowDown')
      })
      expect(result.current.focusedIndex).toBe(1)

      // Press '/' to clear focus and focus composer
      act(() => {
        fireKeyDown('/')
      })

      expect(result.current.focusedIndex).toBe(null)
      expect(mockFocus).toHaveBeenCalled()
    })

    it('slash key works even when no focus exists', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 5,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      expect(result.current.focusedIndex).toBe(null)

      act(() => {
        fireKeyDown('/')
      })

      expect(result.current.focusedIndex).toBe(null)
      expect(mockFocus).toHaveBeenCalled()
    })
  })

  describe('Arrow focus + slash clear chain', () => {
    it('full chain: ArrowDown twice, ArrowUp once, slash clears', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 10,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      // Start with no focus
      expect(result.current.focusedIndex).toBe(null)

      // ArrowDown twice: 0 -> 1
      act(() => {
        fireKeyDown('ArrowDown')
      })
      expect(result.current.focusedIndex).toBe(0)

      act(() => {
        fireKeyDown('ArrowDown')
      })
      expect(result.current.focusedIndex).toBe(1)

      // ArrowUp once: 1 -> 0
      act(() => {
        fireKeyDown('ArrowUp')
      })
      expect(result.current.focusedIndex).toBe(0)

      // Slash clears focus
      act(() => {
        fireKeyDown('/')
      })
      expect(result.current.focusedIndex).toBe(null)
      expect(mockFocus).toHaveBeenCalled()
    })
  })

  describe('j/k keys (Vim-style) still work', () => {
    it('j moves focus down', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 5,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      act(() => {
        fireKeyDown('j')
      })
      expect(result.current.focusedIndex).toBe(0)

      act(() => {
        fireKeyDown('j')
      })
      expect(result.current.focusedIndex).toBe(1)
    })

    it('k moves focus up', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 5,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      // Start at last saying
      act(() => {
        fireKeyDown('k')
      })
      expect(result.current.focusedIndex).toBe(4)

      act(() => {
        fireKeyDown('k')
      })
      expect(result.current.focusedIndex).toBe(3)
    })
  })

  describe('g/G keys for first/last navigation', () => {
    it('g jumps to first saying', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 5,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      act(() => {
        fireKeyDown('k') // Go to last
      })
      expect(result.current.focusedIndex).toBe(4)

      act(() => {
        fireKeyDown('g')
      })
      expect(result.current.focusedIndex).toBe(0)
    })

    it('G (shift+g) jumps to last saying', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 5,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      act(() => {
        fireKeyDown('G', true)
      })
      expect(result.current.focusedIndex).toBe(4)
    })
  })

  describe('no interference when input/textarea is focused', () => {
    it('does not handle keys when input is focused', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 5,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      // Create a mock input element and focus it
      const input = document.createElement('input')
      document.body.appendChild(input)
      input.focus()

      act(() => {
        fireKeyDown('ArrowDown')
      })

      // Focus should not change because input is focused
      expect(result.current.focusedIndex).toBe(null)

      document.body.removeChild(input)
    })

    it('does not handle keys when textarea is focused', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 5,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      // Create a mock textarea and focus it
      const textarea = document.createElement('textarea')
      document.body.appendChild(textarea)
      textarea.focus()

      act(() => {
        fireKeyDown('ArrowDown')
      })

      // Focus should not change because textarea is focused
      expect(result.current.focusedIndex).toBe(null)

      document.body.removeChild(textarea)
    })
  })

  describe('empty stream behavior', () => {
    it('slash still works with empty stream', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 0,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      act(() => {
        fireKeyDown('/')
      })

      expect(result.current.focusedIndex).toBe(null)
      expect(mockFocus).toHaveBeenCalled()
    })

    it('ArrowDown does nothing with empty stream', () => {
      const { result } = renderHook(() =>
        useKeyboardNav({
          sayingsCount: 0,
          streamRef: streamRef as React.RefObject<HTMLDivElement>,
          inputRef: inputRef as React.RefObject<{ focus: () => void } | null>,
        })
      )

      act(() => {
        fireKeyDown('ArrowDown')
      })

      expect(result.current.focusedIndex).toBe(null)
    })
  })
})
