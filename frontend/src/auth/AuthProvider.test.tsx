import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'

import { AuthProvider } from './AuthProvider'
import { useAuth } from './auth-context'
import { ADMIN, stubApi } from '../test-utils'

function Probe() {
  const { state, request } = useAuth()
  return (
    <>
      <p>state: {state.status}</p>
      <button onClick={() => void request('/containers').catch(() => {})}>Call</button>
    </>
  )
}

function renderProbe() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('AuthProvider', () => {
  it('returns to signed-out when a call gets 401 mid-session', async () => {
    let signedIn = true
    stubApi({
      'GET /setup/status': { body: { admin_created: true } },
      'GET /me': () => (signedIn ? { body: ADMIN } : { status: 401, body: { detail: 'Not signed in.' } }),
      'GET /containers': () => {
        signedIn = false
        return { status: 401, body: { detail: 'Not signed in.' } }
      },
    })
    renderProbe()
    expect(await screen.findByText('state: signed-in')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Call' }))
    expect(await screen.findByText('state: signed-out')).toBeVisible()
  })

  it('stays signed in when a call fails for another reason', async () => {
    stubApi({
      'GET /setup/status': { body: { admin_created: true } },
      'GET /me': { body: ADMIN },
      'GET /containers': { status: 500, body: {} },
    })
    renderProbe()
    await screen.findByText('state: signed-in')
    await userEvent.click(screen.getByRole('button', { name: 'Call' }))
    expect(screen.getByText('state: signed-in')).toBeVisible()
  })
})
