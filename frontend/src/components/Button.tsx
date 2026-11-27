import type { ButtonHTMLAttributes } from 'react'

import { cx } from './cx'
import { Spinner } from './Spinner'

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'md' | 'sm'
  loading?: boolean
}

export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  className,
  type = 'button',
  disabled,
  children,
  ...rest
}: Props) {
  return (
    <button
      {...rest}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cx(
        'vp-btn',
        `vp-btn-${variant}`,
        size === 'sm' && 'vp-btn-sm',
        loading && 'vp-btn-loading',
        className,
      )}
    >
      {loading && <Spinner size={size === 'sm' ? 14 : 16} />}
      {children}
    </button>
  )
}
