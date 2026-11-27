import { ChevronDown, LogOut, Settings, User } from 'lucide-react'
import { useId, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { NavLink, useMatch } from 'react-router'

import { Avatar } from './Avatar'
import { cx } from './cx'
import { Icon } from './Icon'
import { useShell } from './shell-context'

type Props = { name: string; onSignOut: () => void }

/** The signed-in admin at the bottom of the sidebar: Profile and Sign out. */
export function AccountMenu({ name, onSignOut }: Props) {
  const shell = useShell()
  const onProfile = useMatch('/profile') !== null
  const onSettings = useMatch('/settings') !== null
  const onAdminPage = onProfile || onSettings
  const [open, setOpen] = useState(onAdminPage)
  const menuId = useId()
  const buttonRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  function onKeyDown(event: KeyboardEvent) {
    if (event.key === 'Escape' && open) {
      event.stopPropagation()
      setOpen(false)
      buttonRef.current?.focus()
    } else if (open && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
      const items = Array.from(menuRef.current?.querySelectorAll<HTMLElement>('a, button') ?? [])
      const at = items.indexOf(document.activeElement as HTMLElement)
      const step = event.key === 'ArrowDown' ? 1 : -1
      items[(at + step + items.length) % items.length]?.focus()
      event.preventDefault()
    }
  }

  return (
    <div className={cx('vp-account', open && 'vp-account-open')} onKeyDown={onKeyDown}>
      <button
        type="button"
        ref={buttonRef}
        className="vp-account-btn"
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => setOpen(!open)}
      >
        <Avatar name={name} />
        <span className="vp-account-name">{name}</span>
        <Icon icon={ChevronDown} className="vp-account-chevron" />
      </button>
      <div id={menuId} ref={menuRef} className="vp-account-menu" hidden={!open}>
        <ul className="vp-sidenav-list" aria-label="Account">
          <li>
            <NavLink
              to="/profile"
              className={({ isActive }) => cx('vp-sidenav-item', isActive && 'vp-sidenav-item-on')}
              onClick={() => shell?.close()}
            >
              <Icon icon={User} />
              <span className="vp-sidenav-label">Profile</span>
            </NavLink>
          </li>
          <li>
            <NavLink
              to="/settings"
              className={({ isActive }) => cx('vp-sidenav-item', isActive && 'vp-sidenav-item-on')}
              onClick={() => shell?.close()}
            >
              <Icon icon={Settings} />
              <span className="vp-sidenav-label">Settings</span>
            </NavLink>
          </li>
          <li>
            <button
              type="button"
              className="vp-sidenav-item"
              onClick={() => {
                shell?.close()
                onSignOut()
              }}
            >
              <Icon icon={LogOut} />
              <span className="vp-sidenav-label">Sign out</span>
            </button>
          </li>
        </ul>
      </div>
    </div>
  )
}
