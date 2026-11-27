import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { SOURCE_NEVER, aiSettings, findingItem, findingList, hostPayload, metricsPayload } from '../test-fixtures'
import { ADMIN, renderApp, stubApi } from '../test-utils'

const STARTING = hostPayload({
  latest: null,
  containers: { total: 0, running: 0, needs_attention: 0 },
  capture: { interval_seconds: 15, sources: { host: SOURCE_NEVER, docker: SOURCE_NEVER } },
})

function signedIn(extra: Parameters<typeof stubApi>[0] = {}) {
  let signedOut = false
  return stubApi({
    'GET /setup/status': { body: { admin_created: true } },
    'GET /me': () => (signedOut ? { status: 401, body: { detail: 'Not signed in.' } } : { body: ADMIN }),
    'GET /host': { body: STARTING },
    'GET /host/metrics?range=1h': { body: metricsPayload() },
    'POST /auth/sign-out': () => {
      signedOut = true
      return { status: 204 }
    },
    ...extra,
  })
}

describe('App shell', () => {
  it("shows the admin's display name and the navigation", async () => {
    signedIn()
    renderApp('/')
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: 'Overview' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('button', { name: /admin/ })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main')
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main')
  })

  it('has one page heading and a calm empty state on Overview', async () => {
    signedIn()
    renderApp('/')
    expect(await screen.findByRole('heading', { level: 1, name: 'Overview' })).toBeVisible()
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(await screen.findByText('Nothing to show yet')).toBeVisible()
    expect(screen.getByText('Vegapunk is starting its first capture.')).toBeVisible()
  })

  it('opens the account menu and signs out', async () => {
    signedIn()
    renderApp('/')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /admin/ }))
    expect(screen.getByRole('link', { name: 'Profile' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Sign out' }))
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeVisible()
  })

  it('closes the account menu on Escape and restores focus', async () => {
    signedIn()
    renderApp('/')
    const user = userEvent.setup()
    const toggle = await screen.findByRole('button', { name: /admin/ })
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await user.keyboard('{Escape}')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveFocus()
  })

  it('moves between menu items with the arrow keys', async () => {
    signedIn()
    renderApp('/')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /admin/ }))
    screen.getByRole('link', { name: 'Profile' }).focus()
    await user.keyboard('{ArrowDown}')
    expect(screen.getByRole('link', { name: 'Settings' })).toHaveFocus()
    await user.keyboard('{ArrowDown}')
    expect(screen.getByRole('button', { name: 'Sign out' })).toHaveFocus()
    await user.keyboard('{ArrowDown}')
    expect(screen.getByRole('link', { name: 'Profile' })).toHaveFocus() // wraps round
    await user.keyboard('{ArrowUp}')
    expect(screen.getByRole('button', { name: 'Sign out' })).toHaveFocus()
  })

  it('opens the account menu by default on Profile', async () => {
    signedIn()
    renderApp('/profile')
    const toggle = await screen.findByRole('button', { name: /admin/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('link', { name: 'Profile' })).toHaveAttribute('aria-current', 'page')
  })

  it('opens the navigation drawer on small screens and closes it on Escape', async () => {
    signedIn()
    renderApp('/')
    const user = userEvent.setup()
    const menu = await screen.findByRole('button', { name: 'Open navigation' })
    await user.click(menu)
    expect(menu).toHaveAttribute('aria-expanded', 'true')
    await user.keyboard('{Escape}')
    expect(menu).toHaveAttribute('aria-expanded', 'false')
    expect(menu).toHaveFocus()
  })

  it('has Settings in the account menu, between Profile and Sign out', async () => {
    signedIn()
    renderApp('/')
    await userEvent.click(await screen.findByRole('button', { name: /admin/ }))
    const items = within(screen.getByRole('list', { name: 'Account' })).getAllByRole('listitem')
    expect(items.map((i) => i.textContent)).toEqual(['Profile', 'Settings', 'Sign out'])
  })

  it('opens the account menu by default on Settings and marks it', async () => {
    signedIn({ 'GET /settings/ai': { body: aiSettings() } })
    renderApp('/settings')
    const toggle = await screen.findByRole('button', { name: /admin/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('link', { name: 'Settings' })).toHaveAttribute('aria-current', 'page')
  })

  it('shows the open findings count in the navigation, and no badge at zero', async () => {
    signedIn({ 'GET /findings?status=open': { body: findingList([findingItem(), findingItem({ id: 'b' })]) } })
    const { unmount } = renderApp('/')
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    const link = await within(nav).findByRole('link', { name: 'Findings, 2 open' })
    expect(await within(link).findByText('2')).toBeVisible()
    unmount()

    signedIn()
    renderApp('/')
    const quiet = within(await screen.findByRole('navigation', { name: 'Main' })).getByRole('link', {
      name: 'Findings',
    })
    expect(quiet.textContent).toBe('Findings')
  })
})
