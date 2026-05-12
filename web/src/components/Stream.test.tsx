import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, render, screen, waitFor } from '@testing-library/react'
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

const DOCUMENTED_MERMAID_MARKDOWN = `\`\`\`mermaid
flowchart TD
  A --> B
\`\`\``

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

describe('Stream Mermaid Markdown rendering', () => {
  beforeEach(() => {
    Object.defineProperty(SVGElement.prototype, 'getBBox', {
      configurable: true,
      value: () => ({ x: 0, y: 0, width: 120, height: 48 }),
    })
  })

  it('renders documented mermaid fences in saying content as diagrams, not ordinary code', () => {
    const mermaidSaying: Saying = {
      ...makeSaying(1),
      content: DOCUMENTED_MERMAID_MARKDOWN,
    }

    const { container } = render(
      <Stream sayings={[mermaidSaying]} connectionStatus="live" tableStatus="open" />
    )

    expect(container.querySelector('code.language-mermaid')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Mermaid diagram')).toBeInTheDocument()
  })

  it('produces sanitized SVG DOM output for saying.content Mermaid fences', async () => {
    const mermaidSaying: Saying = {
      ...makeSaying(1),
      content: DOCUMENTED_MERMAID_MARKDOWN,
    }

    const { container } = render(
      <Stream sayings={[mermaidSaying]} connectionStatus="live" tableStatus="open" />
    )

    await waitFor(() => {
      expect(container.querySelector('.mc-mermaid-diagram svg')).toBeInTheDocument()
    })
    expect(container.querySelector('.mc-mermaid-diagram script')).not.toBeInTheDocument()
    expect(container.querySelector('.mc-mermaid-diagram [onload]')).not.toBeInTheDocument()
  })

  it('preserves raw HTML escaping, inline code, and non-Mermaid fenced code', () => {
    const mixedSaying: Saying = {
      ...makeSaying(1),
      content: 'Inline `mermaid` stays code.\n\n```ts\nconst x = 1\n```\n\n<script>alert(1)</script>',
    }

    const { container } = render(
      <Stream sayings={[mixedSaying]} connectionStatus="live" tableStatus="open" />
    )

    expect(screen.getByText('mermaid')).toBeInTheDocument()
    expect(container.querySelector('code.language-ts')).toHaveTextContent('const x = 1')
    expect(container.querySelector('[aria-label="Mermaid diagram"]')).not.toBeInTheDocument()
    expect(container.querySelector('script')).not.toBeInTheDocument()
    expect(container.innerHTML).toContain('&lt;script')
  })

  it('strips Mermaid init directives before rendering in the stream path', async () => {
    const initDirectiveSaying: Saying = {
      ...makeSaying(1),
      content: '```mermaid\n%%{init: {"theme": "dark"}}%%\nflowchart TD\n  A --> B\n```',
    }

    const { container } = render(
      <Stream sayings={[initDirectiveSaying]} connectionStatus="live" tableStatus="open" />
    )

    await waitFor(() => {
      expect(container.querySelector('.mc-mermaid-diagram svg')).toBeInTheDocument()
    })
    expect(container).not.toHaveTextContent('%%{init')
    expect(container).not.toHaveTextContent('theme')
  })

  it('shows the MermaidRenderer fallback for invalid Mermaid without restoring code-fence rendering', async () => {
    const invalidMermaidSaying: Saying = {
      ...makeSaying(1),
      content: '```mermaid\nnot a valid mermaid diagram\n```',
    }

    const { container } = render(
      <Stream sayings={[invalidMermaidSaying]} connectionStatus="live" tableStatus="open" />
    )

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Failed to render diagram')
    })
    expect(container.querySelector('code.language-mermaid')).not.toBeInTheDocument()
    expect(screen.getByText('not a valid mermaid diagram')).toBeInTheDocument()
  })
})
