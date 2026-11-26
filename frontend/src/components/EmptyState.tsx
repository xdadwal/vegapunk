import { Box } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

import { Icon } from './Icon'

type Props = { title: string; children?: ReactNode; action?: ReactNode; icon?: LucideIcon }

/** Fills a page that has nothing to show, and says why. */
export function EmptyState({ title, children, action, icon = Box }: Props) {
  return (
    <div className="vp-empty vp-empty-empty">
      <div className="vp-empty-icon">
        <Icon icon={icon} size={24} />
      </div>
      <h2 className="vp-empty-title">{title}</h2>
      {children && <div className="vp-empty-text">{children}</div>}
      {action && <div className="vp-empty-actions">{action}</div>}
    </div>
  )
}
