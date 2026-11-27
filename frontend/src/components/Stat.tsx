import type { LucideIcon } from 'lucide-react'
import { Link } from 'react-router'

import { Icon } from './Icon'
import { Sparkline } from './Sparkline'

type Props = {
  label: string
  value: string | number | null
  unit?: string
  hint?: string
  icon?: LucideIcon
  trend?: (number | null)[]
  trendLabel?: string
  /** Make the whole card (when it has `vp-card-linked`) a link to this path. */
  to?: string
}

/** A labelled metric. A missing value shows a dash and no unit. */
export function Stat({ label, value, unit, hint, icon, trend, trendLabel, to }: Props) {
  return (
    <div className="vp-stat">
      <div className="vp-stat-label">
        {icon && <Icon icon={icon} />}
        {to ? (
          <Link className="vp-card-link" to={to}>
            {label}
          </Link>
        ) : (
          label
        )}
      </div>
      <div className="vp-stat-row">
        <div className="vp-stat-main">
          <span className="vp-stat-value">
            {value ?? '—'}
            {value !== null && unit && <span className="vp-stat-unit">{unit}</span>}
          </span>
        </div>
        {trend && <Sparkline values={trend} label={trendLabel ?? `${label}, recent trend`} />}
      </div>
      {hint && <div className="vp-stat-hint">{hint}</div>}
    </div>
  )
}
