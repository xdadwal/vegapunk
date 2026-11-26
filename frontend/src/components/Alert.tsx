import type { ReactNode } from 'react'

type Tone = 'info' | 'success' | 'warning' | 'danger'

const MARK: Record<Tone, string> = { info: 'i', success: '✓', warning: '!', danger: '×' }

type Props = { tone: Tone; title: string; children?: ReactNode; action?: ReactNode }

export function Alert({ tone, title, children, action }: Props) {
  return (
    <div
      className={`vp-alert vp-alert-${tone}`}
      role={tone === 'danger' || tone === 'warning' ? 'alert' : 'status'}
    >
      <span className="vp-alert-mark" aria-hidden="true">
        {MARK[tone]}
      </span>
      <div className="vp-alert-body">
        <div className="vp-alert-title">{title}</div>
        {children && <div className="vp-alert-text">{children}</div>}
      </div>
      {action && <div className="vp-alert-action">{action}</div>}
    </div>
  )
}
