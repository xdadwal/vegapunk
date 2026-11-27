import { Menu } from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { cx } from './cx'
import { Icon } from './Icon'
import { ShellContext } from './shell-context'

type Props = { nav: ReactNode; children: ReactNode }

/** Sidebar on wide screens; below 768px a top bar with a drawer. Only <main> scrolls. */
export function AppShell({ nav, children }: Props) {
  const [open, setOpen] = useState(false)
  const navId = useId()
  const menuRef = useRef<HTMLButtonElement>(null)
  const navRef = useRef<HTMLDivElement>(null)
  const wasOpen = useRef(false)

  useEffect(() => {
    if (open) {
      navRef.current?.querySelector<HTMLElement>('a, button')?.focus()
      const onKey = (event: KeyboardEvent) => {
        if (event.key === 'Escape') setOpen(false)
      }
      document.addEventListener('keydown', onKey)
      wasOpen.current = true
      return () => document.removeEventListener('keydown', onKey)
    }
    if (wasOpen.current) {
      menuRef.current?.focus()
      wasOpen.current = false
    }
  }, [open])

  const shell = useMemo(() => ({ close: () => setOpen(false) }), [])

  return (
    <ShellContext.Provider value={shell}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className={cx('vp-shell', open && 'vp-shell-open')}>
        <header className="vp-shell-bar">
          <button
            type="button"
            ref={menuRef}
            className="vp-btn vp-btn-ghost vp-shell-menu"
            aria-label="Open navigation"
            aria-expanded={open}
            aria-controls={navId}
            onClick={() => setOpen(true)}
          >
            <Icon icon={Menu} size={20} />
          </button>
          <span className="vp-wordmark">Vegapunk</span>
        </header>
        <div className="vp-shell-scrim" aria-hidden="true" onClick={() => setOpen(false)} />
        <div id={navId} ref={navRef} className="vp-shell-nav">
          {nav}
        </div>
        <main className="vp-shell-main" id="main" tabIndex={-1}>
          {children}
        </main>
      </div>
    </ShellContext.Provider>
  )
}
