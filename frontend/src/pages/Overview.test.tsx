import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { SOURCE_NEVER, findingItem, findingList, hostPayload, metricsPayload } from '../test-fixtures'
import { ADMIN, renderApp, stubApi } from '../test-utils'

const BASE = {
  'GET /setup/status': { body: { admin_created: true } },
  'GET /me': { body: ADMIN },
  'GET /host/metrics?range=1h': { body: metricsPayload() },
}

function withHost(host: unknown, extra: Parameters<typeof stubApi>[0] = {}) {
  return stubApi({ ...BASE, 'GET /host': typeof host === 'function' ? (host as never) : { body: host }, ...extra })
}

/** The stat card with this label. Labels like CPU also appear in the chart legend. */
function statCard(label: string): HTMLElement {
  const card = screen
    .getAllByText(label)
    .map((el) => el.closest('.vp-stat'))
    .find(Boolean)
  if (!card) throw new Error(`No stat card labelled ${label}`)
  return card as HTMLElement
}

async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('Overview', () => {
  it('makes the containers card a link to the Containers page, as a lifting card', async () => {
    withHost(hostPayload())
    renderApp('/')
    const link = await screen.findByRole('link', { name: 'Containers running' })
    expect(link).toHaveAttribute('href', '/containers')
    expect(link.closest('.vp-card')).toHaveClass('vp-card-linked')
  })

  it('shows the host, the four stats and the chart', async () => {
    withHost(hostPayload())
    renderApp('/')
    expect(await screen.findByText('nas-01 · TrueNAS SCALE · Docker 29.0 · up 41 days')).toBeVisible()
    expect(screen.getByRole('heading', { level: 1, name: 'Overview' })).toBeVisible()
    const stat = (label: string) => statCard(label)
    expect(within(stat('Containers running')).getByText('11 of 12')).toBeVisible()
    expect(within(stat('CPU')).getByText('23')).toBeVisible()
    expect(within(stat('Memory used')).getByText('25')).toBeVisible()
    expect(within(stat('Memory used')).getByText('4.0 GiB of 16.0 GiB')).toBeVisible()
    expect(within(stat('Storage used')).getByText('40')).toBeVisible()
    expect(within(stat('Storage used')).getByText('8.0 GiB of 20.0 GiB')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'CPU and memory, last hour' })).toBeVisible()
    expect(await screen.findByRole('img', { name: 'CPU and memory, last hour' })).toBeInTheDocument()
    expect(screen.getByText(/^Last capture \d+s ago$/)).toBeVisible()
  })

  it('draws a trend on each stat from the last hour', async () => {
    withHost(hostPayload())
    renderApp('/')
    expect(await screen.findByRole('img', { name: 'CPU, last hour' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Memory, last hour' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Storage, last hour' })).toBeInTheDocument()
  })

  it('shows a dash and no percentage when CPU is not known yet', async () => {
    const host = hostPayload()
    host.latest!.cpu_pct = null
    withHost(host)
    renderApp('/')
    await screen.findByText('Containers running')
    const cpu = statCard('CPU')
    expect(within(cpu).getByText('—')).toBeVisible()
    expect(within(cpu).queryByText('%')).not.toBeInTheDocument()
  })

  it.each([
    [0, null],
    [1, '1 needs attention'],
    [3, '3 need attention'],
  ])('says how many containers need attention (%i)', async (count, text) => {
    withHost(hostPayload({ containers: { total: 12, running: 12 - count, needs_attention: count } }))
    renderApp('/')
    await screen.findByText('Containers running')
    if (text) expect(screen.getByText(text)).toBeVisible()
    else expect(screen.queryByText(/need/)).not.toBeInTheDocument()
  })

  it('shows the Docker error', async () => {
    const host = hostPayload()
    host.capture.sources.docker = {
      ok: false,
      stale: false,
      last_success_at: null,
      error: "Can't reach Docker at unix:///x.",
    }
    withHost(host)
    renderApp('/')
    const alert = await screen.findByRole('alert')
    expect(within(alert).getByText("Can't reach Docker")).toBeVisible()
    expect(within(alert).getByText("Can't reach Docker at unix:///x.")).toBeVisible()
  })

  it('shows when host metrics cannot be read', async () => {
    const host = hostPayload()
    host.capture.sources.host = { ok: false, stale: false, last_success_at: null, error: 'Permission denied' }
    withHost(host)
    renderApp('/')
    expect(await screen.findByText("Can't read host metrics")).toBeVisible()
    expect(screen.getByText('Permission denied')).toBeVisible()
  })

  it('warns when a source has gone stale', async () => {
    const host = hostPayload()
    host.capture.sources.docker = { ok: true, stale: true, last_success_at: '2026-01-01T15:12:00Z', error: null }
    withHost(host)
    renderApp('/')
    expect(await screen.findByText('Data is out of date')).toBeVisible()
    expect(screen.getByText('Nothing has been captured since 3:12 PM UTC.')).toBeVisible()
  })

  it('does not call a failing source stale as well', async () => {
    const host = hostPayload()
    host.capture.sources.docker = { ok: false, stale: true, last_success_at: '2026-01-01T15:12:00Z', error: 'down' }
    withHost(host)
    renderApp('/')
    await screen.findByText("Can't reach Docker")
    expect(screen.queryByText('Data is out of date')).not.toBeInTheDocument()
  })

  it('shows the starting-up message before the first capture', async () => {
    withHost(
      hostPayload({
        latest: null,
        hostname: null,
        os: null,
        docker_version: null,
        boot_time: null,
        containers: { total: 0, running: 0, needs_attention: 0 },
        capture: { interval_seconds: 15, sources: { host: SOURCE_NEVER, docker: SOURCE_NEVER } },
      }),
    )
    renderApp('/')
    expect(await screen.findByText('Nothing to show yet')).toBeVisible()
    expect(screen.getByText('Vegapunk is starting its first capture.')).toBeVisible()
    expect(screen.queryByText('Containers running')).not.toBeInTheDocument()
  })

  it('shows dashes, not nothing, when only Docker has been read', async () => {
    const host = hostPayload({ latest: null })
    host.capture.sources.host = { ok: false, stale: false, last_success_at: null, error: 'Permission denied' }
    withHost(host)
    renderApp('/')
    expect(await screen.findByText('Containers running')).toBeVisible()
    expect(screen.queryByText('Nothing to show yet')).not.toBeInTheDocument()
  })

  it('shows a retryable error when the first load fails, then recovers', async () => {
    let attempts = 0
    withHost(() => {
      attempts += 1
      return attempts === 1 ? { networkError: true } : { body: hostPayload() }
    })
    renderApp('/')
    expect(await screen.findByText("Couldn't load the overview")).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Containers running')).toBeVisible()
  })

  it('says it is loading rather than showing a blank page', async () => {
    withHost(() => new Promise<never>(() => {}))
    renderApp('/')
    expect(await screen.findByRole('heading', { name: 'Overview' })).toBeVisible()
    expect(screen.getByRole('status')).toHaveTextContent('Loading')
  })
})

describe('Overview polling', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('polls again after five seconds and clears a Docker error when Docker returns', async () => {
    let call = 0
    const { fetchMock } = withHost(() => {
      call += 1
      const host = hostPayload()
      if (call === 1)
        host.capture.sources.docker = { ok: false, stale: false, last_success_at: null, error: "Can't reach Docker" }
      return { body: host }
    })
    renderApp('/')
    await tick(0)
    expect(screen.getByText("Can't reach Docker", { selector: '.vp-alert-title' })).toBeVisible()
    await tick(5000)
    expect(screen.queryByText("Can't reach Docker")).not.toBeInTheDocument()
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/host')).toHaveLength(2)
  })

  it('keeps the last data and warns when a later poll fails', async () => {
    let call = 0
    withHost(() => {
      call += 1
      return call === 1 ? { body: hostPayload() } : { networkError: true }
    })
    renderApp('/')
    await tick(0)
    expect(screen.getByText('11 of 12')).toBeVisible()
    await tick(5000)
    expect(screen.getByText('11 of 12')).toBeVisible()
    expect(screen.getByText("Showing the last data we had. Couldn't reach the server.")).toBeVisible()
  })

  it('removes the warning when the server comes back', async () => {
    let call = 0
    withHost(() => {
      call += 1
      return call === 2 ? { networkError: true } : { body: hostPayload() }
    })
    renderApp('/')
    await tick(0)
    await tick(5000)
    expect(screen.getByText(/Showing the last data we had/)).toBeVisible()
    await tick(5000)
    expect(screen.queryByText(/Showing the last data we had/)).not.toBeInTheDocument()
  })
})

describe('Overview needs attention', () => {
  const open = (n: number) =>
    findingList(
      Array.from({ length: n }, (_, i) => findingItem({ id: `f/${i}`, title: `Problem ${i}`, subject: `svc${i}` })),
    )

  it('shows no card when nothing is open', async () => {
    withHost(hostPayload())
    renderApp('/')
    await screen.findByRole('link', { name: 'Containers running' })
    expect(screen.queryByRole('heading', { name: 'Needs attention' })).not.toBeInTheDocument()
  })

  it('lists at most three open findings, each linked, and links to all', async () => {
    withHost(hostPayload(), { 'GET /findings?status=open': { body: open(5) } })
    renderApp('/')
    const heading = await screen.findByRole('heading', { level: 2, name: 'Needs attention' })
    const card = heading.closest('.vp-card') as HTMLElement
    const links = within(card).getAllByRole('link')
    expect(links.map((l) => l.textContent)).toEqual(['Problem 0', 'Problem 1', 'Problem 2', 'See all 5 findings'])
    expect(links[0]).toHaveAttribute('href', '/findings/f%2F0')
    expect(within(card).getByText('svc0')).toHaveClass('vp-mono')
    expect(links[3]).toHaveAttribute('href', '/findings')
  })

  it('says "See the finding" for one', async () => {
    withHost(hostPayload(), { 'GET /findings?status=open': { body: open(1) } })
    renderApp('/')
    expect(await screen.findByRole('link', { name: 'See the finding' })).toHaveAttribute('href', '/findings')
  })

  it('shows nothing extra when the findings request fails', async () => {
    withHost(hostPayload(), { 'GET /findings?status=open': { status: 500, body: { detail: 'boom' } } })
    renderApp('/')
    expect(await screen.findByRole('link', { name: 'Containers running' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: 'Needs attention' })).not.toBeInTheDocument()
  })
})
