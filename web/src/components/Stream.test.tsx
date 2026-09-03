import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

const LABELED_MERMAID_MARKDOWN = `\`\`\`mermaid
flowchart TD
  subgraph API[API Layer]
    A[Gateway Service] --> B[Worker Node]
  end
  B -->|publishes| C[Event Bus]
\`\`\``

const LONG_LABELED_MERMAID_MARKDOWN = `\`\`\`mermaid
flowchart TD
  FM[Human Facility Manager] --> LPA[Lease & Policy Authority]
  LPA -->|governs| DCIM[DCIM Cognitive Core / Agent Fleet]
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
    Object.defineProperty(SVGElement.prototype, 'getComputedTextLength', {
      configurable: true,
      value: function getComputedTextLength() {
        return (this.textContent ?? '').length * 8
      },
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

  it('renders Mermaid SVGs inside the scroll/readability styling hook', async () => {
    const mermaidSaying: Saying = {
      ...makeSaying(1),
      content: DOCUMENTED_MERMAID_MARKDOWN,
    }

    const { container } = render(
      <Stream sayings={[mermaidSaying]} connectionStatus="live" tableStatus="open" />
    )

    const diagram = screen.getByLabelText('Mermaid diagram')
    expect(diagram).toHaveClass('mc-mermaid-diagram')

    await waitFor(() => {
      expect(container.querySelector('.mc-mermaid-diagram > svg')).toBeInTheDocument()
    })
  })

  it('applies readable presentation attributes after Mermaid SVG sanitization', async () => {
    const mermaidSaying: Saying = {
      ...makeSaying(1),
      content: DOCUMENTED_MERMAID_MARKDOWN,
    }

    const { container } = render(
      <Stream sayings={[mermaidSaying]} connectionStatus="live" tableStatus="open" />
    )

    await waitFor(() => {
      expect(container.querySelector('.mc-mermaid-diagram svg .node rect')).toBeInTheDocument()
    })

    const nodeShape = container.querySelector('.mc-mermaid-diagram svg .node rect')
    const labelText = container.querySelector('.mc-mermaid-diagram svg text')
    expect(nodeShape).toHaveAttribute('fill', '#1e3a5f')
    expect(nodeShape).toHaveAttribute('stroke', '#93c5fd')
    expect(labelText).toHaveAttribute('fill', '#f8fafc')
  })

  it('preserves node-internal Mermaid labels after sanitization', async () => {
    const mermaidSaying: Saying = {
      ...makeSaying(1),
      content: LABELED_MERMAID_MARKDOWN,
    }

    const { container } = render(
      <Stream sayings={[mermaidSaying]} connectionStatus="live" tableStatus="open" />
    )

    await waitFor(() => {
      expect(container.querySelector('.mc-mermaid-diagram svg .node text')).toBeInTheDocument()
    })

    const diagram = container.querySelector('.mc-mermaid-diagram svg')
    const nodeLabels = Array.from(diagram?.querySelectorAll('.node text') ?? []).map((node) =>
      node.textContent?.replace(/\s+/g, ' ').trim()
    )

    expect(nodeLabels).toEqual(
      expect.arrayContaining(['Gateway Service', 'Worker Node', 'Event Bus'])
    )
    expect(diagram?.querySelector('foreignObject')).not.toBeInTheDocument()
    expect(diagram?.querySelector('style')).not.toBeInTheDocument()
  })

  it('wraps long node-internal Mermaid labels with SVG tspans while keeping unsafe tags absent', async () => {
    const mermaidSaying: Saying = {
      ...makeSaying(1),
      content: LONG_LABELED_MERMAID_MARKDOWN,
    }

    const { container } = render(
      <Stream sayings={[mermaidSaying]} connectionStatus="live" tableStatus="open" />
    )

    await waitFor(() => {
      expect(container.querySelector('.mc-mermaid-diagram svg .node text tspan')).toBeInTheDocument()
    })

    const diagram = container.querySelector('.mc-mermaid-diagram svg')
    const nodeLabelTexts = Array.from(diagram?.querySelectorAll('.node text') ?? [])
    const wrappedNodeLabels = nodeLabelTexts.map((text) =>
      Array.from(text.querySelectorAll('tspan')).map((tspan) => tspan.textContent?.trim())
    )

    expect(wrappedNodeLabels).toEqual(
      expect.arrayContaining([
        ['Human Facility', 'Manager'],
        ['Lease & Policy', 'Authority'],
        ['DCIM Cognitive', 'Core /Agent Fleet'],
      ])
    )
    expect(
      nodeLabelTexts.every((text) =>
        Array.from(text.querySelectorAll('tspan')).every(
          (tspan) => (tspan.textContent?.length ?? 0) <= 18
        )
      )
    ).toBe(true)
    for (const text of nodeLabelTexts) {
      const lines = Array.from(text.querySelectorAll('tspan')).map(
        (tspan) => tspan.textContent?.trim() ?? ''
      )
      const rect = text.closest('.node')?.querySelector('rect')
      const requiredWidth = Math.max(...lines.map((line) => line.length)) * 8 + 32
      expect(Number(rect?.getAttribute('width'))).toBeGreaterThanOrEqual(requiredWidth)
    }
    const edgeLabelTexts = Array.from(diagram?.querySelectorAll('.edgeLabel') ?? []).map((label) =>
      label.textContent?.replace(/\s+/g, ' ').trim()
    )
    expect(edgeLabelTexts).toContain('governs')
    expect(diagram?.querySelector('foreignObject')).not.toBeInTheDocument()
    expect(diagram?.querySelector('style')).not.toBeInTheDocument()
    expect(diagram?.querySelector('[style]')).not.toBeInTheDocument()
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

  it('shows a Mermaid error while falling back to normal source code display', async () => {
    const invalidMermaidSaying: Saying = {
      ...makeSaying(1),
      content: '```mermaid\nnot a valid mermaid diagram\n```',
    }

    const { container } = render(
      <Stream sayings={[invalidMermaidSaying]} connectionStatus="live" tableStatus="open" />
    )

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Failed to render Mermaid diagram')
    })
    const fallbackCode = container.querySelector('pre.mc-mermaid-source code.language-mermaid')
    expect(fallbackCode).toBeInTheDocument()
    expect(fallbackCode).toHaveTextContent('not a valid mermaid diagram')
  })
})

describe('Stream Markdown attachments', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('stays metadata-only until expansion and uses the safe Markdown/Mermaid pipeline', async () => {
    const content = [
      '# Attachment heading',
      '',
      '<script>alert(1)</script>',
      '',
      '[unsafe](javascript:alert(2))',
      '',
      '```mermaid',
      'flowchart TD',
      '  A --> B',
      '```',
    ].join('\n')
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: 'attachment-1',
          saying_id: 'saying-1',
          table_id: 'table-1',
          name: 'notes.md',
          position: 0,
          byte_size: new TextEncoder().encode(content).byteLength,
          media_type: 'text/markdown',
          content,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      )
    )
    vi.stubGlobal('fetch', fetchMock)
    const saying: Saying = {
      ...makeSaying(1),
      attachments: [
        {
          id: 'attachment-1',
          name: 'notes.md',
          position: 0,
          byte_size: new TextEncoder().encode(content).byteLength,
          media_type: 'text/markdown',
        },
      ],
    }

    const { container } = render(
      <Stream sayings={[saying]} connectionStatus="live" tableStatus="open" />
    )

    const disclosure = screen.getByRole('button', { name: /notes\.md/ })
    expect(disclosure).toHaveAttribute('aria-expanded', 'false')
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.queryByRole('heading', { name: 'Attachment heading' })).not.toBeInTheDocument()

    fireEvent.click(disclosure)

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(fetchMock.mock.calls[0][0]).toBe(
      '/api/v1/tables/table-1/sayings/saying-1/attachments/attachment-1'
    )
    expect(await screen.findByRole('heading', { name: 'Attachment heading' })).toBeInTheDocument()
    expect(container.querySelector('script')).not.toBeInTheDocument()
    expect(container.innerHTML).toContain('&lt;script')
    expect(screen.getByText('unsafe').closest('a')?.getAttribute('href')).not.toContain(
      'javascript:'
    )
    await waitFor(() => {
      expect(container.querySelector('.mc-attachment-body .mc-mermaid-diagram svg')).toBeInTheDocument()
    })

    fireEvent.click(disclosure)
    fireEvent.click(disclosure)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
