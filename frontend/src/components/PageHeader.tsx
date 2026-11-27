import type { ReactNode } from 'react'

type Props = { title: string; description?: string; actions?: ReactNode }

/** The top of every page. Its title is the page's only <h1>. */
export function PageHeader({ title, description, actions }: Props) {
  return (
    <div className="vp-pagehead">
      <div className="vp-pagehead-row">
        <div className="vp-pagehead-main">
          <div className="vp-pagehead-titleline">
            <h1 className="vp-pagehead-title">{title}</h1>
          </div>
          {description && <p className="vp-pagehead-desc">{description}</p>}
        </div>
        {actions && <div className="vp-pagehead-actions">{actions}</div>}
      </div>
    </div>
  )
}
