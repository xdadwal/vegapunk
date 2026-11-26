import { useId } from 'react'
import type { SelectHTMLAttributes } from 'react'

import { cx } from './cx'

type Option = { value: string; label: string }

type Props = Omit<SelectHTMLAttributes<HTMLSelectElement>, 'children'> & {
  label: string
  options: Option[]
}

/** A labelled native dropdown for a short, fixed list. */
export function Select({ label, options, className, id, ...rest }: Props) {
  const generated = useId()
  const fieldId = id ?? generated
  return (
    <div className={cx('vp-field', className)}>
      <label className="vp-label" htmlFor={fieldId}>
        {label}
      </label>
      <div className="vp-select-wrap">
        <select {...rest} id={fieldId} className="vp-input vp-select">
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}
