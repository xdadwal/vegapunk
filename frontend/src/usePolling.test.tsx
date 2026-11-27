import { act, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'

import { AuthProvider } from './auth/AuthProvider'
import { ADMIN, stubApi } from './test-utils'
import { usePolling } from './usePolling'

function Probe({ path = '/thing', intervalMs }: { path?: string; intervalMs?: number }) {
  const { data, error, loading } = usePolling<{ n: number }>(path, { intervalMs })
  return (
    <p>
      {loading ? 'loading' : 'loaded'} | data: {data ? data.n : 'none'} | error: {error ? error.detail : 'none'}
    </p>
  )
}

function renderProbe(props: Parameters<typeof Probe>[0] = {}) {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <Probe {...props} />
      </AuthProvider>
    </MemoryRouter>,
  )
}

const BASE = {
  'GET /setup/status': { body: { admin_created: true } },
  'GET /me': { body: ADMIN },
}

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

function setHidden(hidden: boolean) {
  Object.defineProperty(document, 'hidden', { value: hidden, configurable: true })
  document.dispatchEvent(new Event('visibilitychange'))
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  vi.useRealTimers()
  setHidden(false)
})

describe('usePolling', () => {
  it('loads, then keeps polling', async () => {
    let n = 0
    const { fetchMock } = stubApi({ ...BASE, 'GET /thing': () => ({ body: { n: ++n } }) })
    renderProbe()
    await tick(0)
    expect(screen.getByText(/loaded \| data: 1/)).toBeVisible()
    await tick(5000)
    expect(screen.getByText(/data: 2/)).toBeVisible()
    await tick(5000)
    expect(screen.getByText(/data: 3/)).toBeVisible()
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/thing')).toHaveLength(3)
  })

  it('starts out loading', async () => {
    stubApi({ ...BASE, 'GET /thing': () => new Promise<never>(() => {}) })
    renderProbe()
    await tick(0)
    expect(screen.getByText(/^loading \| data: none/)).toBeVisible()
  })

  it('keeps the last data when a poll fails and clears the error on success', async () => {
    let call = 0
    stubApi({
      ...BASE,
      'GET /thing': () => {
        call += 1
        if (call === 2) return { status: 500, body: { detail: 'Boom' } }
        return { body: { n: call } }
      },
    })
    renderProbe()
    await tick(0)
    expect(screen.getByText(/data: 1 \| error: none/)).toBeVisible()
    await tick(5000)
    expect(screen.getByText(/data: 1 \| error: Boom/)).toBeVisible()
    await tick(5000)
    expect(screen.getByText(/data: 3 \| error: none/)).toBeVisible()
  })

  it('reports a first-load failure with no data', async () => {
    stubApi({ ...BASE, 'GET /thing': { networkError: true } })
    renderProbe()
    await tick(0)
    expect(screen.getByText(/loaded \| data: none \| error: Couldn't reach the server\./)).toBeVisible()
  })

  it('does not start a request while one is in flight', async () => {
    const { fetchMock } = stubApi({ ...BASE, 'GET /thing': () => new Promise<never>(() => {}) })
    renderProbe()
    await tick(30_000)
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/thing')).toHaveLength(1)
  })

  it('pauses while the tab is hidden and refreshes when it is shown again', async () => {
    let n = 0
    const { fetchMock } = stubApi({ ...BASE, 'GET /thing': () => ({ body: { n: ++n } }) })
    renderProbe()
    await tick(0)
    setHidden(true)
    await tick(30_000)
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/thing')).toHaveLength(1)
    setHidden(false)
    await tick(0)
    expect(screen.getByText(/data: 2/)).toBeVisible()
  })

  it('stops polling on unmount', async () => {
    const { fetchMock } = stubApi({ ...BASE, 'GET /thing': { body: { n: 1 } } })
    const view = renderProbe()
    await tick(0)
    view.unmount()
    await tick(30_000)
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/thing')).toHaveLength(1)
  })

  it('clears data and reloads when the path changes', async () => {
    stubApi({
      ...BASE,
      'GET /a': { body: { n: 1 } },
      'GET /b': () => new Promise<never>(() => {}),
    })
    const view = renderProbe({ path: '/a' })
    await tick(0)
    expect(screen.getByText(/data: 1/)).toBeVisible()
    view.rerender(
      <MemoryRouter>
        <AuthProvider>
          <Probe path="/b" />
        </AuthProvider>
      </MemoryRouter>,
    )
    await tick(0)
    expect(screen.getByText(/^loading \| data: none/)).toBeVisible()
  })

  it('uses the interval it is given', async () => {
    let n = 0
    stubApi({ ...BASE, 'GET /thing': () => ({ body: { n: ++n } }) })
    renderProbe({ intervalMs: 30_000 })
    await tick(0)
    await tick(29_000)
    expect(screen.getByText(/data: 1/)).toBeVisible()
    await tick(1_000)
    expect(screen.getByText(/data: 2/)).toBeVisible()
  })

  it('sends the person to Sign in when the session has ended', async () => {
    let signedIn = true
    stubApi({
      'GET /setup/status': { body: { admin_created: true } },
      'GET /me': () => (signedIn ? { body: ADMIN } : { status: 401, body: { detail: 'Not signed in.' } }),
      'GET /thing': () => {
        signedIn = false
        return { status: 401, body: { detail: 'Not signed in.' } }
      },
    })
    render(
      <MemoryRouter>
        <AuthProvider>
          <Probe />
          <AuthStatus />
        </AuthProvider>
      </MemoryRouter>,
    )
    await tick(0)
    expect(screen.getByText('auth: signed-out')).toBeVisible()
  })
})

import { useAuth } from './auth/auth-context'
function AuthStatus() {
  return <p>auth: {useAuth().state.status}</p>
}
