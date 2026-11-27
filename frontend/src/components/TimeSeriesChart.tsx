import { useEffect, useState } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'

import { formatBytes, formatDay, formatDayTime, formatPercent, formatTime } from '../format'
import { Button } from './Button'

type Point = { ts: string; value: number | null }
export type ChartSeries = { name: string; color: string; points: Point[] }

type Props = {
  series: ChartSeries[]
  unit: '%' | 'bytes'
  label: string
  yMax?: number
  height?: number
  /** Label the time axis with dates instead of times (for ranges of days). */
  dateAxis?: boolean
}

const TOP = 10
const BOTTOM = 26
const LEFT = 60
const RIGHT = 12
const FALLBACK_WIDTH = 600

function niceMax(max: number): number {
  if (!(max > 0)) return 1
  const step = Math.pow(10, Math.floor(Math.log10(max / 4)))
  for (const m of [1, 2, 2.5, 5, 10]) if (m * step * 4 >= max) return m * step * 4
  return 40 * step
}

function useWidth() {
  // The element is held in state, so the observer attaches whenever the plot appears: it is
  // not rendered while there is no data, and may be replaced when switching to the table.
  const [node, setNode] = useState<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    if (!node || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => setWidth(node.clientWidth))
    observer.observe(node)
    return () => observer.disconnect()
  }, [node])
  return [setNode, width || FALLBACK_WIDTH] as const
}

/** Lines over time. Readable by keyboard (arrow keys) and as a table. */
export function TimeSeriesChart({ series, unit, label, yMax, height = 200, dateAxis = false }: Props) {
  const [wrapRef, width] = useWidth()
  const [selected, setIndex] = useState<number | null>(null)
  const [asTable, setAsTable] = useState(false)

  const times = series[0]?.points.map((p) => p.ts) ?? []
  const n = times.length
  const values = series.flatMap((s) => s.points.map((p) => p.value)).filter((v): v is number => v !== null)

  if (values.length === 0) {
    return (
      <div className="vp-chart">
        <p className="vp-stat-hint">No data for this range yet.</p>
      </div>
    )
  }

  // A refresh can bring fewer points than the one being read; keep the selection in range.
  const index = selected === null ? null : Math.min(selected, n - 1)

  const fmtValue = (v: number) => (unit === '%' ? formatPercent(v) : formatBytes(v))
  // Axis ticks over days name the day; everywhere a single point is read, the time too.
  const fmtAxis = (iso: string) => (dateAxis ? formatDay(iso) : formatTime(iso))
  const fmtTime = (iso: string) => (dateAxis ? formatDayTime(iso) : formatTime(iso))
  const top = yMax ?? niceMax(Math.max(...values))
  const plotHeight = height - TOP - BOTTOM
  const plotWidth = Math.max(10, width - LEFT - RIGHT)
  const t0 = new Date(times[0]).getTime()
  const t1 = new Date(times[n - 1]).getTime()
  const xOf = (i: number) => LEFT + (n > 1 ? (new Date(times[i]).getTime() - t0) / (t1 - t0 || 1) : 0.5) * plotWidth
  const yOf = (v: number) => TOP + plotHeight - (v / top) * plotHeight
  const multi = series.length > 1

  function pathFor(s: ChartSeries): string {
    let d = ''
    let drawing = false
    s.points.forEach((p, i) => {
      if (p.value === null) {
        drawing = false
        return
      }
      const at = `${xOf(i).toFixed(1)} ${yOf(p.value).toFixed(1)}`
      // A run's first point also draws a zero-length line, so a lone point shows as a dot.
      d += drawing ? `L${at}` : `M${at}L${at}`
      drawing = true
    })
    return d
  }

  function reading(i: number): string {
    const parts = series.map((s) => {
      const v = s.points[i].value
      return `${multi ? `${s.name} ` : ''}${v === null ? 'no data' : fmtValue(v)}`
    })
    return `${fmtTime(times[i])}: ${parts.join(', ')}`
  }

  function onPointerMove(event: PointerEvent<SVGSVGElement>) {
    const x = event.clientX - event.currentTarget.getBoundingClientRect().left
    let best = 0
    for (let i = 1; i < n; i++) if (Math.abs(xOf(i) - x) < Math.abs(xOf(best) - x)) best = i
    setIndex(best)
  }

  function onKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    if (event.key === 'Escape') return setIndex(null)
    const at = index ?? n - 1
    const next =
      event.key === 'ArrowLeft'
        ? at - 1
        : event.key === 'ArrowRight'
          ? at + 1
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? n - 1
              : null
    if (next === null) return
    event.preventDefault()
    setIndex(Math.max(0, Math.min(n - 1, next)))
  }

  const tickCount = Math.max(2, Math.min(5, Math.floor(plotWidth / 110)))
  const xTicks = Array.from({ length: tickCount }, (_, k) => Math.round((k * (n - 1)) / (tickCount - 1)))

  const header = (
    <div className="vp-chart-head">
      <div className="vp-chart-tools">
        {multi && (
          <ul className="vp-legend" aria-label="Legend">
            {series.map((s) => (
              <li key={s.name}>
                <span className="vp-key-line" style={{ background: s.color }} />
                {s.name}
              </li>
            ))}
          </ul>
        )}
        <Button size="sm" variant="ghost" aria-pressed={asTable} onClick={() => setAsTable(!asTable)}>
          {asTable ? 'Chart' : 'Table'}
        </Button>
      </div>
    </div>
  )

  if (asTable) {
    return (
      <div className="vp-chart">
        {header}
        <div className="vp-chart-table">
          <table className="vp-table">
            <caption className="vp-sr-only">{label}</caption>
            <thead>
              <tr>
                <th scope="col">Time</th>
                {series.map((s) => (
                  <th key={s.name} scope="col" className="vp-td-right">
                    {s.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {times.map((t, i) => (
                <tr key={t}>
                  <th scope="row" className="vp-th-row vp-mono vp-td-mono">
                    {fmtTime(t)}
                  </th>
                  {series.map((s) => (
                    <td key={s.name} className="vp-td-right">
                      {s.points[i].value === null ? '—' : fmtValue(s.points[i].value as number)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    )
  }

  const flip = index !== null && xOf(index) > width / 2
  return (
    <div className="vp-chart">
      {header}
      <div ref={wrapRef} className="vp-chart-plot" style={{ height }}>
        <svg
          width={width}
          height={height}
          className="vp-chart-svg"
          tabIndex={0}
          role="img"
          aria-label={label}
          onPointerMove={onPointerMove}
          onPointerLeave={() => setIndex(null)}
          onKeyDown={onKeyDown}
          onFocus={() => setIndex((i) => i ?? n - 1)}
          onBlur={() => setIndex(null)}
        >
          {[0, 1, 2, 3, 4].map((k) => {
            const value = (top * k) / 4
            const y = Math.round(yOf(value)) + 0.5
            return (
              <g key={k}>
                <line
                  x1={LEFT}
                  x2={LEFT + plotWidth}
                  y1={y}
                  y2={y}
                  className={k === 0 ? 'vp-chart-base' : 'vp-chart-grid'}
                />
                <text x={LEFT - 8} y={y} className="vp-chart-tick" textAnchor="end" dominantBaseline="middle">
                  {fmtValue(value)}
                </text>
              </g>
            )
          })}
          {xTicks.map((i, k) => (
            <text
              key={k}
              x={xOf(i)}
              y={height - 8}
              className="vp-chart-tick"
              textAnchor={k === 0 ? 'start' : k === xTicks.length - 1 ? 'end' : 'middle'}
            >
              {fmtAxis(times[i])}
            </text>
          ))}
          {series.map((s) => (
            <path
              key={s.name}
              d={pathFor(s)}
              fill="none"
              style={{ stroke: s.color }}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          ))}
          {index !== null && (
            <g>
              <line x1={xOf(index)} x2={xOf(index)} y1={TOP} y2={TOP + plotHeight} className="vp-chart-cross" />
              {series.map((s) => {
                const v = s.points[index].value
                return v === null ? null : (
                  <circle
                    key={s.name}
                    cx={xOf(index)}
                    cy={yOf(v)}
                    r={4}
                    className="vp-chart-dot"
                    style={{ fill: s.color }}
                  />
                )
              })}
            </g>
          )}
        </svg>
        {index !== null && (
          <div
            className="vp-chart-tip"
            style={flip ? { right: width - xOf(index) + 12, top: TOP } : { left: xOf(index) + 12, top: TOP }}
          >
            <div className="vp-chart-tip-time vp-mono">{fmtTime(times[index])}</div>
            {series.map((s) => (
              <div key={s.name} className="vp-chart-tip-row">
                <span className="vp-key-line" style={{ background: s.color }} />
                <strong>{s.points[index].value === null ? '—' : fmtValue(s.points[index].value as number)}</strong>
                <span className="vp-chart-tip-name">{s.name}</span>
              </div>
            ))}
          </div>
        )}
        <div className="vp-sr-only" aria-live="polite">
          {index !== null ? reading(index) : ''}
        </div>
      </div>
    </div>
  )
}
