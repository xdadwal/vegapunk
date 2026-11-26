import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ADMIN, renderApp, stubApi } from '../test-utils'

function signedIn(extra: Parameters<typeof stubApi>[0] = {}) {
  return stubApi({
    'GET /setup/status': { body: { admin_created: true } },
    'GET /me': { body: ADMIN },
    ...extra,
  })
}

describe('Profile', () => {
  it('saves details and updates the sidebar name', async () => {
    const { sent } = signedIn({
      'PATCH /me': ({ body }) => ({ body: { ...ADMIN, ...(body as object) } }),
    })
    renderApp('/profile')
    const user = userEvent.setup()
    const name = await screen.findByLabelText('Display name')
    await user.clear(name)
    await user.type(name, 'Akira')
    await user.click(screen.getByRole('button', { name: 'Save details' }))
    expect(await screen.findByText('Details saved')).toBeVisible()
    expect(sent('PATCH /me')).toEqual([{ display_name: 'Akira', username: 'admin' }])
    expect(screen.getByRole('button', { name: /Akira/ })).toBeVisible()
  })

  it('checks details before sending', async () => {
    const { sent } = signedIn()
    renderApp('/profile')
    const user = userEvent.setup()
    const name = await screen.findByLabelText('Display name')
    await user.clear(name)
    await user.type(name, '   ')
    await user.clear(screen.getByLabelText('Username'))
    await user.type(screen.getByLabelText('Username'), 'Not Valid')
    await user.click(screen.getByRole('button', { name: 'Save details' }))
    expect(screen.getByLabelText('Display name')).toHaveAccessibleDescription(
      'Enter a name of 1 to 64 characters.',
    )
    expect(screen.getByLabelText('Username')).toHaveAccessibleDescription(
      'Use 1 to 32 lowercase letters, numbers, dashes or underscores.',
    )
    expect(sent('PATCH /me')).toHaveLength(0)
  })

  it("shows the server's message when saving fails", async () => {
    signedIn({ 'PATCH /me': { networkError: true } })
    renderApp('/profile')
    await userEvent.click(await screen.findByRole('button', { name: 'Save details' }))
    expect(await screen.findByText("Couldn't reach the server.")).toBeVisible()
  })

  it('changes the password and clears the fields', async () => {
    const { sent } = signedIn({ 'POST /me/password': { status: 204 } })
    renderApp('/profile')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Current password'), 'correct horse')
    await user.type(screen.getByLabelText('New password'), 'battery staple')
    await user.type(screen.getByLabelText('Confirm new password'), 'battery staple')
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText('Password changed. Other sessions were signed out.')).toBeVisible()
    expect(sent('POST /me/password')).toEqual([
      { current_password: 'correct horse', new_password: 'battery staple' },
    ])
    expect(screen.getByLabelText('Current password')).toHaveValue('')
    expect(screen.getByLabelText('New password')).toHaveValue('')
  })

  it("shows the server's message for a wrong current password", async () => {
    signedIn({
      'POST /me/password': { status: 400, body: { detail: "Your current password doesn't match." } },
    })
    renderApp('/profile')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Current password'), 'wrong')
    await user.type(screen.getByLabelText('New password'), 'battery staple')
    await user.type(screen.getByLabelText('Confirm new password'), 'battery staple')
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByLabelText('Current password')).toHaveAccessibleDescription(
      "Your current password doesn't match.",
    )
    expect(screen.queryByText(/Password changed/)).not.toBeInTheDocument()
  })

  it('blocks a mismatched confirmation without calling the API', async () => {
    const { sent } = signedIn()
    renderApp('/profile')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Current password'), 'correct horse')
    await user.type(screen.getByLabelText('New password'), 'battery staple')
    await user.type(screen.getByLabelText('Confirm new password'), 'battery stapl')
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    expect(screen.getByLabelText('Confirm new password')).toHaveAccessibleDescription(
      "The two passwords don't match.",
    )
    expect(sent('POST /me/password')).toHaveLength(0)
  })

  it('blocks a short new password without calling the API', async () => {
    const { sent } = signedIn()
    renderApp('/profile')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Current password'), 'correct horse')
    await user.type(screen.getByLabelText('New password'), 'short')
    await user.type(screen.getByLabelText('Confirm new password'), 'short')
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    expect(screen.getByLabelText('New password')).toHaveAccessibleDescription('Use at least 10 characters.')
    expect(sent('POST /me/password')).toHaveLength(0)
  })

  it('sends you to Sign in if the session ended while you were on the page', async () => {
    let signedOut = false
    signedIn({
      'GET /me': () => (signedOut ? { status: 401, body: { detail: 'Not signed in.' } } : { body: ADMIN }),
      'PATCH /me': () => {
        signedOut = true
        return { status: 401, body: { detail: 'Not signed in.' } }
      },
    })
    renderApp('/profile')
    await userEvent.click(await screen.findByRole('button', { name: 'Save details' }))
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeVisible()
  })
})
