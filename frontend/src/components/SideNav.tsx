import { Gauge } from 'lucide-react'
import type { ReactNode } from 'react'
import { NavLink } from 'react-router'

import { cx } from './cx'
import { Icon } from './Icon'
import { useShell } from './shell-context'

/** The wordmark, the main areas, and the account menu pinned to the bottom. */
export function SideNav({ account }: { account: ReactNode }) {
  const shell = useShell()
  return (
    <nav className="vp-sidenav" aria-label="Main">
      <div className="vp-sidenav-brand">
        <span className="vp-wordmark">Vegapunk</span>
      </div>
      <div className="vp-sidenav-body">
        <div className="vp-sidenav-section">
          <ul className="vp-sidenav-list">
            <li>
              <NavLink
                to="/"
                end
                className={({ isActive }) => cx('vp-sidenav-item', isActive && 'vp-sidenav-item-on')}
                onClick={() => shell?.close()}
              >
                <Icon icon={Gauge} />
                <span className="vp-sidenav-label">Overview</span>
              </NavLink>
            </li>
          </ul>
        </div>
      </div>
      <div className="vp-sidenav-account">{account}</div>
    </nav>
  )
}
