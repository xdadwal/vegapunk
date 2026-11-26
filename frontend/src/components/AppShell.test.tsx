import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ADMIN, renderApp, stubApi } from '../test-utils'

function signedIn(extra: Parameters<typeof stubApi>[0] = {}) {
  let signedOut = false
  return stubApi({
    'GET /setup/status': { body: { admin_created: true } },
    'GET /me': () => (signedOut ? { status: 401, body: { detail: 'Not signed in.' } } : { body: ADMIN }),
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
    expect(screen.getByText('Nothing to show yet')).toBeVisible()
    expect(screen.getByText("Vegapunk isn't capturing data from this host yet.")).toBeVisible()
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
    expect(screen.getByRole('button', { name: 'Sign out' })).toHaveFocus()
    await user.keyboard('{ArrowUp}')
    expect(screen.getByRole('link', { name: 'Profile' })).toHaveFocus()
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
})
