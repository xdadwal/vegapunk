import { act, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Gauge } from 'lucide-react'

import { Sparkline } from './Sparkline'
import { Stat } from './Stat'
import { StatusBadge } from './StatusBadge'
import { TimeSeriesChart } from './TimeSeriesChart'

describe('StatusBadge', () => {
  it.each([
    ['online', 'Online'],
    ['degraded', 'Degraded'],
    ['offline', 'Offline'],
    ['unknown', 'Unknown'],
  ] as const)('shows the word as well as the dot for %s', (status, word) => {
    render(<StatusBadge status={status} />)
    expect(screen.getByText(word)).toBeVisible()
  })
})

describe('Stat', () => {
  it('shows the label, value, unit and hint', () => {
    render(<Stat label="CPU" value={23} unit="%" hint="across 8 cores" icon={Gauge} />)
    expect(screen.getByText('CPU')).toBeVisible()
    expect(screen.getByText('23')).toBeVisible()
    expect(screen.getByText('%')).toBeVisible()
    expect(screen.getByText('across 8 cores')).toBeVisible()
  })

  it('shows a dash for a missing value, with no unit', () => {
    render(<Stat label="CPU" value={null} unit="%" />)
    expect(screen.getByText('—')).toBeVisible()
    expect(screen.queryByText('%')).not.toBeInTheDocument()
  })

  it('draws a trend when it has one', () => {
    render(<Stat label="CPU" value={5} trend={[1, 2, 3]} trendLabel="CPU, last hour" />)
    expect(screen.getByRole('img', { name: 'CPU, last hour' })).toBeInTheDocument()
  })
})

describe('Sparkline', () => {
  it('draws nothing for fewer than two points', () => {
    const { container } = render(<Sparkline values={[1]} label="x" />)
    expect(container.querySelector('svg')).toBeNull()
    const none = render(<Sparkline values={[null, null, 4]} label="x" />)
    expect(none.container.querySelector('svg')).toBeNull()
  })

  it('breaks the line at missing values', () => {
    const { container } = render(<Sparkline values={[1, 2, null, 4, 5]} label="trend" />)
    const path = container.querySelector('path')?.getAttribute('d') ?? ''
    expect(path.match(/M/g)).toHaveLength(2)
  })

  it('is labelled for screen readers', () => {
    render(<Sparkline values={[1, 2, 3]} label="Memory, last hour" />)
    expect(screen.getByRole('img', { name: 'Memory, last hour' })).toBeInTheDocument()
  })
})

const T = (minutes: number) => new Date(Date.UTC(2026, 0, 1, 12, minutes)).toISOString()
const SERIES = [
  {
    name: 'CPU',
    color: 'var(--chart-1)',
    points: [
      { ts: T(0), value: 10 },
      { ts: T(5), value: 20 },
      { ts: T(10), value: null },
      { ts: T(15), value: 40 },
    ],
  },
  {
    name: 'Memory',
    color: 'var(--chart-2)',
    points: [
      { ts: T(0), value: 50 },
      { ts: T(5), value: 55 },
      { ts: T(10), value: 60 },
      { ts: T(15), value: 65 },
    ],
  },
]

describe('TimeSeriesChart', () => {
  it('lists the same values in its table view', async () => {
    render(<TimeSeriesChart series={SERIES} unit="%" label="CPU and memory" />)
    await userEvent.click(screen.getByRole('button', { name: 'Table' }))
    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('row')).toHaveLength(5) // header + 4 times
    expect(within(table).getByText('12:00 PM')).toBeVisible()
    expect(within(table).getByText('40%')).toBeVisible()
    expect(within(table).getAllByText('—')).toHaveLength(1) // the missing CPU value
    await userEvent.click(screen.getByRole('button', { name: 'Chart' }))
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('reads a point with the arrow keys', () => {
    render(<TimeSeriesChart series={SERIES} unit="%" label="CPU and memory" />)
    const chart = screen.getByRole('img', { name: 'CPU and memory' })
    fireEvent.focus(chart)
    const live = () => document.querySelector('[aria-live="polite"]')?.textContent
    expect(live()).toBe('12:15 PM: CPU 40%, Memory 65%')
    fireEvent.keyDown(chart, { key: 'ArrowLeft' })
    expect(live()).toBe('12:10 PM: CPU no data, Memory 60%')
    fireEvent.keyDown(chart, { key: 'Home' })
    expect(live()).toBe('12:00 PM: CPU 10%, Memory 50%')
    fireEvent.keyDown(chart, { key: 'ArrowLeft' })
    expect(live()).toBe('12:00 PM: CPU 10%, Memory 50%') // does not go past the start
    fireEvent.keyDown(chart, { key: 'Escape' })
    expect(live()).toBe('')
  })

  it('formats byte values', async () => {
    const series = [
      {
        name: 'Memory',
        color: 'var(--chart-1)',
        points: [
          { ts: T(0), value: 1024 ** 3 },
          { ts: T(5), value: 2 * 1024 ** 3 },
        ],
      },
    ]
    render(<TimeSeriesChart series={series} unit="bytes" label="Memory" />)
    await userEvent.click(screen.getByRole('button', { name: 'Table' }))
    expect(screen.getByText('2.0 GiB')).toBeVisible()
  })

  it('shows an empty message with no points', () => {
    render(<TimeSeriesChart series={[{ name: 'CPU', color: 'x', points: [] }]} unit="%" label="CPU" />)
    expect(screen.getByText('No data for this range yet.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Table' })).not.toBeInTheDocument()
  })

  it('shows the empty message when every value is missing', () => {
    const series = [
      {
        name: 'CPU',
        color: 'x',
        points: [
          { ts: T(0), value: null },
          { ts: T(5), value: null },
        ],
      },
    ]
    render(<TimeSeriesChart series={series} unit="%" label="CPU" />)
    expect(screen.getByText('No data for this range yet.')).toBeVisible()
  })

  it('labels the series in a legend when there are several', () => {
    render(<TimeSeriesChart series={SERIES} unit="%" label="CPU and memory" />)
    const legend = screen.getByRole('list', { name: 'Legend' })
    expect(within(legend).getByText('CPU')).toBeVisible()
    expect(within(legend).getByText('Memory')).toBeVisible()
  })

  it('survives a refresh with fewer points while a point is selected', () => {
    const { rerender } = render(<TimeSeriesChart series={SERIES} unit="%" label="CPU and memory" />)
    fireEvent.focus(screen.getByRole('img', { name: 'CPU and memory' }))
    const shorter = SERIES.map((s) => ({ ...s, points: s.points.slice(0, 3) }))
    expect(() => rerender(<TimeSeriesChart series={shorter} unit="%" label="CPU and memory" />)).not.toThrow()
    expect(document.querySelector('[aria-live="polite"]')?.textContent).toBe('12:10 PM: CPU no data, Memory 60%')
    const none = SERIES.map((s) => ({ ...s, points: [] }))
    expect(() => rerender(<TimeSeriesChart series={none} unit="%" label="CPU and memory" />)).not.toThrow()
  })

  it('draws a point that has no neighbours', () => {
    const series = [
      {
        name: 'CPU',
        color: 'x',
        points: [
          { ts: T(0), value: null },
          { ts: T(5), value: 7 },
          { ts: T(10), value: null },
        ],
      },
    ]
    const { container } = render(<TimeSeriesChart series={series} unit="%" label="CPU" />)
    expect(container.querySelector('svg path')?.getAttribute('d')).toMatch(/^M[\d. ]+L[\d. ]+$/)
  })

  it('labels hourly points with the day and the time, not the day alone', async () => {
    const series = [
      {
        name: 'CPU',
        color: 'x',
        points: [
          { ts: T(0), value: 1 },
          { ts: T(60), value: 2 },
        ],
      },
    ]
    render(<TimeSeriesChart series={series} unit="%" label="CPU" dateAxis />)
    fireEvent.focus(screen.getByRole('img', { name: 'CPU' }))
    expect(document.querySelector('[aria-live="polite"]')?.textContent).toBe('Jan 1, 1:00 PM: 2.0%')
    await userEvent.click(screen.getByRole('button', { name: 'Table' }))
    expect(screen.getByText('Jan 1, 12:00 PM')).toBeVisible()
    expect(screen.getByText('Jan 1, 1:00 PM')).toBeVisible()
  })
})

describe('TimeSeriesChart width', () => {
  class FakeObserver {
    static all: FakeObserver[] = []
    element: Element | null = null
    disconnected = false
    callback: () => void
    constructor(callback: () => void) {
      this.callback = callback
      FakeObserver.all.push(this)
    }
    observe(element: Element) {
      this.element = element
    }
    disconnect() {
      this.disconnected = true
    }
  }
  const live = () => FakeObserver.all.filter((o) => !o.disconnected)

  beforeEach(() => {
    FakeObserver.all = []
    vi.stubGlobal('ResizeObserver', FakeObserver)
  })

  it('follows the size of the chart once data arrives after an empty start', () => {
    const empty = [{ name: 'CPU', color: 'x', points: [] }]
    const { rerender } = render(<TimeSeriesChart series={empty} unit="%" label="CPU" />)
    rerender(<TimeSeriesChart series={SERIES} unit="%" label="CPU" />)
    const plot = document.querySelector('.vp-chart-plot') as HTMLElement
    expect(live().map((o) => o.element)).toContain(plot)
    Object.defineProperty(plot, 'clientWidth', { value: 900, configurable: true })
    act(() =>
      live()
        .find((o) => o.element === plot)
        ?.callback(),
    )
    expect(document.querySelector('svg.vp-chart-svg')?.getAttribute('width')).toBe('900')
  })

  it('keeps following the size after switching to the table and back', async () => {
    render(<TimeSeriesChart series={SERIES} unit="%" label="CPU" />)
    await userEvent.click(screen.getByRole('button', { name: 'Table' }))
    await userEvent.click(screen.getByRole('button', { name: 'Chart' }))
    const plot = document.querySelector('.vp-chart-plot')
    expect(live().map((o) => o.element)).toContain(plot)
  })
})
