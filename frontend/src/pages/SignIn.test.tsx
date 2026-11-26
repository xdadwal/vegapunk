import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ADMIN, renderApp, stubApi } from '../test-utils'

const SIGNED_OUT = {
  'GET /setup/status': { body: { admin_created: true } },
}

async function submit(user: ReturnType<typeof userEvent.setup>, username: string, password: string) {
  await user.type(await screen.findByLabelText('Username'), username)
  await user.type(screen.getByLabelText('Password'), password)
  await user.click(screen.getByRole('button', { name: 'Sign in' }))
}

describe('Sign in', () => {
  it('signs in and enters the app', async () => {
    let signedIn = false
    const { sent } = stubApi({
      ...SIGNED_OUT,
      'GET /me': () => (signedIn ? { body: ADMIN } : { status: 401, body: { detail: 'Not signed in.' } }),
      'POST /auth/sign-in': () => {
        signedIn = true
        return { status: 204 }
      },
    })
    renderApp('/')
    await submit(userEvent.setup(), 'admin', 'correct horse')
    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeVisible()
    expect(sent('POST /auth/sign-in')).toEqual([{ username: 'admin', password: 'correct horse' }])
  })

  it('shows the mismatch message on 401', async () => {
    stubApi({
      ...SIGNED_OUT,
      'GET /me': { status: 401, body: { detail: 'Not signed in.' } },
      'POST /auth/sign-in': { status: 401, body: { detail: "That username or password doesn't match." } },
    })
    renderApp('/')
    await submit(userEvent.setup(), 'admin', 'wrong')
    expect(await screen.findByLabelText('Password')).toHaveAccessibleDescription(
      "That username or password doesn't match. After 5 tries sign-in pauses for 15 minutes.",
    )
  })

  it('shows when sign-in resumes on 429', async () => {
    stubApi({
      ...SIGNED_OUT,
      'GET /me': { status: 401, body: { detail: 'Not signed in.' } },
      'POST /auth/sign-in': {
        status: 429,
        body: { detail: 'Sign-in is paused after 5 failed tries.', locked_until: '2026-01-01T15:12:00+00:00' },
      },
    })
    renderApp('/')
    await submit(userEvent.setup(), 'admin', 'correct horse')
    expect(await screen.findByText('Sign-in is paused until 3:12 PM UTC.')).toBeVisible()
  })

  it('shows a network failure as a message, not a crash', async () => {
    stubApi({
      ...SIGNED_OUT,
      'GET /me': { status: 401, body: { detail: 'Not signed in.' } },
      'POST /auth/sign-in': { networkError: true },
    })
    renderApp('/')
    await submit(userEvent.setup(), 'admin', 'correct horse')
    expect(await screen.findByText("Couldn't reach the server.")).toBeVisible()
  })

  it('disables the button while the request is in flight', async () => {
    stubApi({
      ...SIGNED_OUT,
      'GET /me': { status: 401, body: { detail: 'Not signed in.' } },
      'POST /auth/sign-in': () => new Promise<never>(() => {}),
    })
    renderApp('/')
    await submit(userEvent.setup(), 'admin', 'correct horse')
    expect(await screen.findByRole('button', { name: /Sign in/ })).toBeDisabled()
  })

  it('tells people how to reset a forgotten password', async () => {
    stubApi({ ...SIGNED_OUT, 'GET /me': { status: 401, body: { detail: 'Not signed in.' } } })
    renderApp('/')
    expect(await screen.findByText('docker exec -it vegapunk python -m vegapunk reset-password')).toBeVisible()
  })
})
