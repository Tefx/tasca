import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { Stream } from './Stream'
import type { Saying } from '../api/sayings'

function makeSaying(sequence: number): Saying {
  return {
    id: `saying-${sequence}`,
    table_id: 'table-1',
    sequence,
    speaker: {
      kind: 'human',
      name: 'Human',
      patron_id: null,
    },
    content: `Message ${sequence}`,
    pinned: false,
    created_at: '2024-01-01T00:00:00Z',
    mentions_unresolved: [],
  }
}

describe('Stream live region announcements', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('exposes non-empty live-region text on empty-to-non-empty transition', () => {
    const { rerender } = render(
      <Stream sayings={[]} connectionStatus="live" tableStatus="open" />
    )

    const liveRegion = screen.getByTestId('stream-live-region')
    expect(liveRegion).toBeInTheDocument()
    expect(liveRegion).toHaveTextContent('')

    rerender(
      <Stream sayings={[makeSaying(1)]} connectionStatus="live" tableStatus="open" />
    )

    act(() => {
      vi.advanceTimersByTime(1999)
    })
    expect(liveRegion).toHaveTextContent('')

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(liveRegion).toHaveTextContent('1 new saying in stream')

    act(() => {
      vi.advanceTimersByTime(3999)
    })
    expect(liveRegion).toHaveTextContent('1 new saying in stream')

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(liveRegion).toHaveTextContent('')
  })

  it('debounces bursts and keeps staggered arrivals sane', () => {
    const { rerender } = render(
      <Stream sayings={[]} connectionStatus="live" tableStatus="open" />
    )

    const liveRegion = screen.getByTestId('stream-live-region')

    rerender(
      <Stream sayings={[makeSaying(1)]} connectionStatus="live" tableStatus="open" />
    )
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(liveRegion).toHaveTextContent('')

    rerender(
      <Stream sayings={[makeSaying(1), makeSaying(2)]} connectionStatus="live" tableStatus="open" />
    )
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(liveRegion).toHaveTextContent('')

    rerender(
      <Stream sayings={[makeSaying(1), makeSaying(2), makeSaying(3)]} connectionStatus="live" tableStatus="open" />
    )
    act(() => {
      vi.advanceTimersByTime(1999)
    })
    expect(liveRegion).toHaveTextContent('')

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(liveRegion).toHaveTextContent('3 new sayings in stream')

    rerender(
      <Stream sayings={[makeSaying(1), makeSaying(2), makeSaying(3), makeSaying(4)]} connectionStatus="live" tableStatus="open" />
    )
    act(() => {
      vi.advanceTimersByTime(1999)
    })
    expect(liveRegion).toHaveTextContent('3 new sayings in stream')

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(liveRegion).toHaveTextContent('1 new saying in stream')

    act(() => {
      vi.advanceTimersByTime(4000)
    })
    expect(liveRegion).toHaveTextContent('')
  })

  it('rebases after decreases so rebound increases announce again', () => {
    const { rerender } = render(
      <Stream sayings={[makeSaying(1), makeSaying(2), makeSaying(3)]} connectionStatus="live" tableStatus="open" />
    )

    const liveRegion = screen.getByTestId('stream-live-region')
    act(() => {
      vi.advanceTimersByTime(2000)
    })
    expect(liveRegion).toHaveTextContent('')

    rerender(
      <Stream sayings={[makeSaying(1), makeSaying(2)]} connectionStatus="live" tableStatus="open" />
    )
    expect(liveRegion).toHaveTextContent('')

    rerender(
      <Stream sayings={[makeSaying(1), makeSaying(2), makeSaying(3)]} connectionStatus="live" tableStatus="open" />
    )

    act(() => {
      vi.advanceTimersByTime(1999)
    })
    expect(liveRegion).toHaveTextContent('')

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(liveRegion).toHaveTextContent('1 new saying in stream')
  })
})
