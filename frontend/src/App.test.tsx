import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ADMIN, renderApp, stubApi } from './test-utils'

describe('App', () => {
  it('shows Setup when no admin exists', async () => {
    stubApi({ 'GET /setup/status': { body: { admin_created: false } } })
    renderApp('/')
    expect(await screen.findByRole('heading', { name: 'Set up your control room' })).toBeVisible()
  })

  it('shows Sign in when signed out, whatever the path', async () => {
    stubApi({
      'GET /setup/status': { body: { admin_created: true } },
      'GET /me': { status: 401, body: { detail: 'Not signed in.' } },
    })
    renderApp('/profile')
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeVisible()
  })

  it('shows the app when signed in and leaves Sign in for Overview', async () => {
    stubApi({
      'GET /setup/status': { body: { admin_created: true } },
      'GET /me': { body: ADMIN },
    })
    renderApp('/sign-in')
    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeVisible()
  })

  it('sends unknown paths to Overview', async () => {
    stubApi({
      'GET /setup/status': { body: { admin_created: true } },
      'GET /me': { body: ADMIN },
    })
    renderApp('/nowhere')
    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeVisible()
  })

  it('shows a load error with Retry, and recovers', async () => {
    let attempts = 0
    stubApi({
      'GET /setup/status': () => {
        attempts += 1
        return attempts === 1 ? { networkError: true } : { body: { admin_created: true } }
      },
      'GET /me': { status: 401, body: { detail: 'Not signed in.' } },
    })
    renderApp('/')
    expect(await screen.findByText("Couldn't load Vegapunk")).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeVisible()
  })

  it('shows the load error when the server fails with 500', async () => {
    stubApi({ 'GET /setup/status': { status: 500, body: {} } })
    renderApp('/')
    expect(await screen.findByText("Couldn't load Vegapunk")).toBeVisible()
  })

  it('tells people it is loading, not blank', () => {
    stubApi({ 'GET /setup/status': () => new Promise<never>(() => {}) })
    renderApp('/')
    expect(screen.getByRole('status')).toHaveTextContent('Loading')
  })
})
