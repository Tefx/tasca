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

// =============================================================================
// Test Utilities
// =============================================================================

type MockRefs = {
  inputRef: React.RefObject<{ focus: () => void } | null>
  streamRef: React.RefObject<HTMLDivElement>
  mockFocus: ReturnType<typeof vi.fn>
  mockScrollIntoView: ReturnType<typeof vi.fn>
}

function createMockRefs(): MockRefs {
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

function fireKeyDown(key: string, shiftKey = false): KeyboardEvent {
  const event = new KeyboardEvent('keydown', { key, shiftKey, bubbles: true })
  document.dispatchEvent(event)
  return event
}

/** Helper to render hook with standard options, reducing boilerplate. */
function renderKeyboardNav(
  sayingsCount: number,
  refs: MockRefs
): ReturnType<typeof renderHook<typeof useKeyboardNav>> {
  return renderHook(() =>
    useKeyboardNav({
      sayingsCount,
      streamRef: refs.streamRef,
      inputRef: refs.inputRef,
    })
  )
}

// =============================================================================
// Tests
// =============================================================================

describe('useKeyboardNav', () => {
  let refs: MockRefs

  beforeEach(() => {
    vi.useFakeTimers()
    refs = createMockRefs()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  describe('ArrowUp/ArrowDown focus navigation', () => {
    it('ArrowDown creates focused state on first saying when no focus exists', () => {
      const { result } = renderKeyboardNav(5, refs)
      expect(result.current.focusedIndex).toBe(null)
      act(() => fireKeyDown('ArrowDown'))
      expect(result.current.focusedIndex).toBe(0)
      expect(refs.mockScrollIntoView).toHaveBeenCalled()
    })

    it('ArrowDown moves focus to next saying', () => {
      const { result } = renderKeyboardNav(5, refs)
      act(() => fireKeyDown('ArrowDown'))
      expect(result.current.focusedIndex).toBe(0)
      act(() => fireKeyDown('ArrowDown'))
      expect(result.current.focusedIndex).toBe(1)
    })

    it('ArrowDown does not exceed max index', () => {
      const { result } = renderKeyboardNav(3, refs)
      act(() => {
        fireKeyDown('ArrowDown')
        fireKeyDown('ArrowDown')
        fireKeyDown('ArrowDown')
      })
      expect(result.current.focusedIndex).toBe(2)
      act(() => fireKeyDown('ArrowDown'))
      expect(result.current.focusedIndex).toBe(2)
    })

    it('ArrowUp creates focused state on last saying when no focus exists', () => {
      const { result } = renderKeyboardNav(5, refs)
      expect(result.current.focusedIndex).toBe(null)
      act(() => fireKeyDown('ArrowUp'))
      expect(result.current.focusedIndex).toBe(4)
      expect(refs.mockScrollIntoView).toHaveBeenCalled()
    })

    it('ArrowUp moves focus to previous saying', () => {
      const { result } = renderKeyboardNav(5, refs)
      act(() => fireKeyDown('ArrowUp'))
      expect(result.current.focusedIndex).toBe(4)
      act(() => fireKeyDown('ArrowUp'))
      expect(result.current.focusedIndex).toBe(3)
    })

    it('ArrowUp does not go below index 0', () => {
      const { result } = renderKeyboardNav(3, refs)
      act(() => fireKeyDown('ArrowDown'))
      expect(result.current.focusedIndex).toBe(0)
      act(() => fireKeyDown('ArrowUp'))
      expect(result.current.focusedIndex).toBe(0)
    })
  })

  describe('/ key clears focus and focuses composer', () => {
    it('slash key focuses input and clears focus index', () => {
      const { result } = renderKeyboardNav(5, refs)
      act(() => {
        fireKeyDown('ArrowDown')
        fireKeyDown('ArrowDown')
      })
      expect(result.current.focusedIndex).toBe(1)
      act(() => fireKeyDown('/'))
      expect(result.current.focusedIndex).toBe(null)
      expect(refs.mockFocus).toHaveBeenCalled()
    })

    it('slash key works even when no focus exists', () => {
      const { result } = renderKeyboardNav(5, refs)
      expect(result.current.focusedIndex).toBe(null)
      act(() => fireKeyDown('/'))
      expect(result.current.focusedIndex).toBe(null)
      expect(refs.mockFocus).toHaveBeenCalled()
    })
  })

  describe('Arrow focus + slash clear chain', () => {
    it('full chain: ArrowDown twice, ArrowUp once, slash clears', () => {
      const { result } = renderKeyboardNav(10, refs)
      expect(result.current.focusedIndex).toBe(null)
      act(() => fireKeyDown('ArrowDown'))
      expect(result.current.focusedIndex).toBe(0)
      act(() => fireKeyDown('ArrowDown'))
      expect(result.current.focusedIndex).toBe(1)
      act(() => fireKeyDown('ArrowUp'))
      expect(result.current.focusedIndex).toBe(0)
      act(() => fireKeyDown('/'))
      expect(result.current.focusedIndex).toBe(null)
      expect(refs.mockFocus).toHaveBeenCalled()
    })
  })

  describe('j/k keys (Vim-style) still work', () => {
    it('j moves focus down', () => {
      const { result } = renderKeyboardNav(5, refs)
      act(() => fireKeyDown('j'))
      expect(result.current.focusedIndex).toBe(0)
      act(() => fireKeyDown('j'))
      expect(result.current.focusedIndex).toBe(1)
    })

    it('k moves focus up', () => {
      const { result } = renderKeyboardNav(5, refs)
      act(() => fireKeyDown('k'))
      expect(result.current.focusedIndex).toBe(4)
      act(() => fireKeyDown('k'))
      expect(result.current.focusedIndex).toBe(3)
    })
  })

  describe('g/G keys for first/last navigation', () => {
    it('g jumps to first saying', () => {
      const { result } = renderKeyboardNav(5, refs)
      act(() => fireKeyDown('k'))
      expect(result.current.focusedIndex).toBe(4)
      act(() => fireKeyDown('g'))
      expect(result.current.focusedIndex).toBe(0)
    })

    it('G (shift+g) jumps to last saying', () => {
      const { result } = renderKeyboardNav(5, refs)
      act(() => fireKeyDown('G', true))
      expect(result.current.focusedIndex).toBe(4)
    })
  })

  describe('no interference when input/textarea is focused', () => {
    it('does not handle keys when input is focused', () => {
      const { result } = renderKeyboardNav(5, refs)
      const input = document.createElement('input')
      document.body.appendChild(input)
      input.focus()
      act(() => fireKeyDown('ArrowDown'))
      expect(result.current.focusedIndex).toBe(null)
      document.body.removeChild(input)
    })

    it('does not handle keys when textarea is focused', () => {
      const { result } = renderKeyboardNav(5, refs)
      const textarea = document.createElement('textarea')
      document.body.appendChild(textarea)
      textarea.focus()
      act(() => fireKeyDown('ArrowDown'))
      expect(result.current.focusedIndex).toBe(null)
      document.body.removeChild(textarea)
    })
  })

  describe('empty stream behavior', () => {
    it('slash still works with empty stream', () => {
      const { result } = renderKeyboardNav(0, refs)
      act(() => fireKeyDown('/'))
      expect(result.current.focusedIndex).toBe(null)
      expect(refs.mockFocus).toHaveBeenCalled()
    })

    it('ArrowDown does nothing with empty stream', () => {
      const { result } = renderKeyboardNav(0, refs)
      act(() => fireKeyDown('ArrowDown'))
      expect(result.current.focusedIndex).toBe(null)
    })
  })
})
