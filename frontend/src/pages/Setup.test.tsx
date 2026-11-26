import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ADMIN, renderApp, stubApi } from '../test-utils'

async function fill(user: ReturnType<typeof userEvent.setup>, username: string, a: string, b: string) {
  await user.type(await screen.findByLabelText('Username'), username)
  await user.type(screen.getByLabelText('Password'), a)
  await user.type(screen.getByLabelText('Confirm password'), b)
  await user.click(screen.getByRole('button', { name: 'Create account' }))
}

describe('Setup', () => {
  it('blocks submit when passwords differ', async () => {
    const { sent } = stubApi({ 'GET /setup/status': { body: { admin_created: false } } })
    renderApp('/')
    await fill(userEvent.setup(), 'admin', 'correct horse', 'correct hors')
    expect(await screen.findByText("The two passwords don't match.")).toBeVisible()
    expect(sent('POST /setup/admin')).toHaveLength(0)
  })

  it('checks the username and password length before sending', async () => {
    const { sent } = stubApi({ 'GET /setup/status': { body: { admin_created: false } } })
    renderApp('/')
    await fill(userEvent.setup(), 'Not Valid', 'short', 'short')
    expect(screen.getByLabelText('Username')).toHaveAccessibleDescription(
      'Use 1 to 32 lowercase letters, numbers, dashes or underscores.',
    )
    expect(screen.getByLabelText('Password')).toHaveAccessibleDescription('Use at least 10 characters.')
    expect(sent('POST /setup/admin')).toHaveLength(0)
  })

  it('creates the account and enters the app', async () => {
    let created = false
    const { sent } = stubApi({
      'GET /setup/status': () => ({ body: { admin_created: created } }),
      'POST /setup/admin': () => {
        created = true
        return { status: 201, body: ADMIN }
      },
      'GET /me': { body: ADMIN },
    })
    renderApp('/')
    await fill(userEvent.setup(), 'admin', 'correct horse', 'correct horse')
    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeVisible()
    expect(sent('POST /setup/admin')).toEqual([{ username: 'admin', password: 'correct horse' }])
  })

  it("shows the server's message on 409 and offers a way out", async () => {
    let created = false
    stubApi({
      'GET /setup/status': () => ({ body: { admin_created: created } }),
      'POST /setup/admin': () => {
        created = true
        return { status: 409, body: { detail: 'The admin account already exists.' } }
      },
      'GET /me': { status: 401, body: { detail: 'Not signed in.' } },
    })
    renderApp('/')
    const user = userEvent.setup()
    await fill(user, 'admin', 'correct horse', 'correct horse')
    expect(await screen.findByText('The admin account already exists.')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Go to sign in' }))
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeVisible()
  })

  it('disables the button while the request is in flight', async () => {
    stubApi({
      'GET /setup/status': { body: { admin_created: false } },
      'POST /setup/admin': () => new Promise<never>(() => {}),
    })
    renderApp('/')
    await fill(userEvent.setup(), 'admin', 'correct horse', 'correct horse')
    expect(await screen.findByRole('button', { name: /Create account/ })).toBeDisabled()
  })
})
