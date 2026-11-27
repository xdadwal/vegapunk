import { Outlet } from 'react-router'

import { useAuth } from '../auth/auth-context'
import { AccountMenu } from './AccountMenu'
import { AppShell } from './AppShell'
import type { FindingList } from '../types'
import { usePolling } from '../usePolling'
import { SideNav } from './SideNav'

/** The frame around every signed-in page. */
export function AppLayout() {
  const { state, signOut } = useAuth()
  const findings = usePolling<FindingList>('/findings?status=open')
  if (state.status !== 'signed-in') return null
  return (
    <AppShell
      nav={
        <SideNav
          openFindings={findings.data?.open_count ?? 0}
          account={<AccountMenu name={state.admin.display_name} onSignOut={() => void signOut()} />}
        />
      }
    >
      <Outlet />
    </AppShell>
  )
}
