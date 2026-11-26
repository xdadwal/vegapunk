import { useId } from 'react'
import type { InputHTMLAttributes } from 'react'

import { cx } from './cx'

type Props = InputHTMLAttributes<HTMLInputElement> & {
  label: string
  hint?: string
  error?: string
  mono?: boolean
}

export function Input({ label, hint, error, mono, className, id, ...rest }: Props) {
  const generated = useId()
  const fieldId = id ?? generated
  const messageId = `${fieldId}-message`
  const message = error ?? hint

  return (
    <div className={cx('vp-field', className)}>
      <label className="vp-label" htmlFor={fieldId}>
        {label}
      </label>
      <input
        {...rest}
        id={fieldId}
        className={cx('vp-input', mono && 'vp-input-mono')}
        aria-invalid={error ? true : undefined}
        aria-describedby={message ? messageId : undefined}
      />
      {message && (
        <div id={messageId} className={cx('vp-hint', error && 'vp-hint-error')}>
          {message}
        </div>
      )}
    </div>
  )
}
