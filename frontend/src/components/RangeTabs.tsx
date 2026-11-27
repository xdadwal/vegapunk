import { useRef } from 'react'
import type { KeyboardEvent } from 'react'

import { RANGES } from '../ranges'
import type { RangeId } from '../ranges'
import { cx } from './cx'

type Props = { value: RangeId; onChange: (range: RangeId) => void }

/** Switches the time range of the charts. Arrow keys move between ranges. */
export function RangeTabs({ value, onChange }: Props) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({})

  function onKeyDown(event: KeyboardEvent) {
    const at = RANGES.findIndex((r) => r.id === value)
    const next =
      event.key === 'ArrowRight'
        ? at + 1
        : event.key === 'ArrowLeft'
          ? at - 1
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? RANGES.length - 1
              : null
    if (next === null) return
    event.preventDefault()
    const target = RANGES[(next + RANGES.length) % RANGES.length]
    onChange(target.id)
    refs.current[target.id]?.focus()
  }

  return (
    <div className="vp-tablist" role="tablist" aria-label="Time range" onKeyDown={onKeyDown}>
      {RANGES.map((r) => {
        const selected = r.id === value
        return (
          <button
            key={r.id}
            ref={(el) => {
              refs.current[r.id] = el
            }}
            type="button"
            role="tab"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            className={cx('vp-tab', selected && 'vp-tab-on')}
            onClick={() => onChange(r.id)}
          >
            {r.label}
          </button>
        )
      })}
    </div>
  )
}
