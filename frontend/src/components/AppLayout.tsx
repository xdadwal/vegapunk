import { Outlet } from 'react-router'

import { useAuth } from '../auth/auth-context'
import { AccountMenu } from './AccountMenu'
import { AppShell } from './AppShell'
import { SideNav } from './SideNav'

/** The frame around every signed-in page. */
export function AppLayout() {
  const { state, signOut } = useAuth()
  if (state.status !== 'signed-in') return null
  return (
    <AppShell
      nav={<SideNav account={<AccountMenu name={state.admin.display_name} onSignOut={() => void signOut()} />} />}
    >
      <Outlet />
    </AppShell>
  )
}
