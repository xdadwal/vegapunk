import { useId, useLayoutEffect, useRef } from 'react'
import type { KeyboardEvent, TextareaHTMLAttributes } from 'react'

import { cx } from './cx'

type Props = Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'onSubmit'> & {
  label: string
  /** Hide the label visually; screen readers still hear it. */
  labelHidden?: boolean
  hint?: string
  error?: string
  mono?: boolean
  /** Enter calls onSubmit and Shift+Enter adds a line. */
  submitOnEnter?: boolean
  onSubmit?: (text: string) => void
  maxHeight?: number
}

/** A labelled multi-line field that grows with its content up to `maxHeight`. */
export function Textarea({
  label,
  labelHidden,
  hint,
  error,
  mono,
  submitOnEnter,
  onSubmit,
  maxHeight = 200,
  className,
  id,
  rows = 3,
  onInput,
  onKeyDown,
  value,
  ...rest
}: Props) {
  const generated = useId()
  const fieldId = id ?? generated
  const messageId = `${fieldId}-message`
  const message = error ?? hint
  const ref = useRef<HTMLTextAreaElement>(null)

  function resize() {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight + 2, maxHeight)}px`
    el.style.overflowY = el.scrollHeight + 2 > maxHeight ? 'auto' : 'hidden'
  }

  useLayoutEffect(resize, [value, maxHeight])

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    onKeyDown?.(event)
    if (event.defaultPrevented || !submitOnEnter) return
    const composing = event.nativeEvent.isComposing
    if (event.key === 'Enter' && !event.shiftKey && !event.altKey && !event.ctrlKey && !event.metaKey && !composing) {
      event.preventDefault()
      onSubmit?.(event.currentTarget.value)
    }
  }

  return (
    <div className={cx('vp-field', className)}>
      <label className={cx('vp-label', labelHidden && 'vp-sr-only')} htmlFor={fieldId}>
        {label}
      </label>
      <textarea
        {...rest}
        ref={ref}
        id={fieldId}
        rows={rows}
        value={value}
        className={cx('vp-input', 'vp-textarea', mono && 'vp-input-mono')}
        aria-invalid={error ? true : undefined}
        aria-describedby={message ? messageId : undefined}
        onInput={(event) => {
          resize()
          onInput?.(event)
        }}
        onKeyDown={handleKeyDown}
      />
      {message && (
        <div id={messageId} className={cx('vp-hint', error && 'vp-hint-error')}>
          {message}
        </div>
      )}
    </div>
  )
}
