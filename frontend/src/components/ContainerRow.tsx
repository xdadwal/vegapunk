import { Link } from 'react-router'

import { usageText } from '../containerText'
import type { ContainerItem } from '../types'
import { StatusBadge } from './StatusBadge'

export function ContainerRow({ container }: { container: ContainerItem }) {
  return (
    <div className="vp-service vp-service-linked" role="row">
      <div className="vp-service-main" role="cell">
        <div className="vp-service-name vp-mono">
          <Link className="vp-card-link" to={`/containers/${encodeURIComponent(container.name)}`}>
            {container.name}
          </Link>
        </div>
        <div className="vp-service-meta vp-mono">{container.image}</div>
        <div className="vp-service-meta">{usageText(container)}</div>
      </div>
      <div className="vp-service-status" role="cell">
        <StatusBadge status={container.status} />
      </div>
    </div>
  )
}
