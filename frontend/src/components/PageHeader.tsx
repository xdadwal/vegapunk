import { ChevronRight } from 'lucide-react'
import type { ReactNode } from 'react'
import { Link } from 'react-router'

import { Icon } from './Icon'

type Crumb = { label: string; to?: string }

type Props = {
  title: string
  description?: string
  actions?: ReactNode
  /** The last crumb is the current page and is not a link. */
  breadcrumbs?: Crumb[]
  /** A machine's name (a container) is set in mono. */
  mono?: boolean
  /** Shown beside the title, such as a status badge. */
  meta?: ReactNode
}

/** The top of every page. Its title is the page's only <h1>. */
export function PageHeader({ title, description, actions, breadcrumbs, mono, meta }: Props) {
  return (
    <div className="vp-pagehead">
      {breadcrumbs && breadcrumbs.length > 0 && (
        <nav className="vp-crumbs" aria-label="Breadcrumb">
          <ol>
            {breadcrumbs.map((crumb, i) => {
              const last = i === breadcrumbs.length - 1
              return (
                <li key={crumb.label}>
                  {crumb.to && !last ? (
                    <Link to={crumb.to}>{crumb.label}</Link>
                  ) : (
                    <span aria-current={last ? 'page' : undefined}>{crumb.label}</span>
                  )}
                  {!last && <Icon icon={ChevronRight} size={14} />}
                </li>
              )
            })}
          </ol>
        </nav>
      )}
      <div className="vp-pagehead-row">
        <div className="vp-pagehead-main">
          <div className="vp-pagehead-titleline">
            <h1 className={mono ? 'vp-pagehead-title vp-mono' : 'vp-pagehead-title'}>{title}</h1>
            {meta}
          </div>
          {description && <p className="vp-pagehead-desc">{description}</p>}
        </div>
        {actions && <div className="vp-pagehead-actions">{actions}</div>}
      </div>
    </div>
  )
}
