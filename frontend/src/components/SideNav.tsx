import { Bot, Boxes, CircleAlert, Gauge } from 'lucide-react'
import type { ReactNode } from 'react'
import { NavLink } from 'react-router'

import { cx } from './cx'
import { Icon } from './Icon'
import { useShell } from './shell-context'

/** The wordmark, the main areas, and the account menu pinned to the bottom. */
export function SideNav({ account, openFindings = 0 }: { account: ReactNode; openFindings?: number }) {
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
            <li>
              <NavLink
                to="/agent"
                className={({ isActive }) => cx('vp-sidenav-item', isActive && 'vp-sidenav-item-on')}
                onClick={() => shell?.close()}
              >
                <Icon icon={Bot} />
                <span className="vp-sidenav-label">Agent</span>
              </NavLink>
            </li>
            <li>
              <NavLink
                to="/containers"
                className={({ isActive }) => cx('vp-sidenav-item', isActive && 'vp-sidenav-item-on')}
                onClick={() => shell?.close()}
              >
                <Icon icon={Boxes} />
                <span className="vp-sidenav-label">Containers</span>
              </NavLink>
            </li>
            <li>
              <NavLink
                to="/findings"
                className={({ isActive }) => cx('vp-sidenav-item', isActive && 'vp-sidenav-item-on')}
                onClick={() => shell?.close()}
              >
                <Icon icon={CircleAlert} />
                <span className="vp-sidenav-label">Findings</span>
                {openFindings > 0 && <span className="vp-tab-count">{openFindings}</span>}
              </NavLink>
            </li>
          </ul>
        </div>
      </div>
      <div className="vp-sidenav-account">{account}</div>
    </nav>
  )
}
