import { useId } from 'react'
import type { ButtonHTMLAttributes } from 'react'

import { cx } from './cx'

type Props = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onChange' | 'role' | 'children'> & {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
}

/** An on/off setting. The label names the setting as something that is true when on. */
export function Switch({ label, checked, onChange, className, id, ...rest }: Props) {
  const generated = useId()
  const switchId = id ?? generated
  return (
    <div className={cx('vp-switch-field', className)}>
      <button
        {...rest}
        id={switchId}
        type="button"
        role="switch"
        aria-checked={checked}
        className="vp-switch"
        onClick={() => onChange(!checked)}
      >
        <span className="vp-switch-thumb" aria-hidden="true" />
      </button>
      <label className="vp-label" htmlFor={switchId}>
        {label}
      </label>
    </div>
  )
}
