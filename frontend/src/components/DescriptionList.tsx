import type { ReactNode } from 'react'

import { cx } from './cx'

export type Fact = { term: string; value: ReactNode; mono?: boolean }

/** Key/value facts about one thing. A missing value shows a dash, never an empty cell. */
export function DescriptionList({ items, label }: { items: Fact[]; label: string }) {
  return (
    <dl className="vp-dl" aria-label={label}>
      {items.map((item) => (
        <div key={item.term} className="vp-dl-row">
          <dt>{item.term}</dt>
          <dd className={cx(item.mono && 'vp-mono')}>{item.value ?? '\u2014'}</dd>
        </div>
      ))}
    </dl>
  )
}
