import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { GIB, detailPayload } from '../test-fixtures'
import { ADMIN, renderApp, stubApi } from '../test-utils'

const MIB = 1024 ** 2

function detail(routes: Parameters<typeof stubApi>[0]) {
  return stubApi({
    'GET /setup/status': { body: { admin_created: true } },
    'GET /me': { body: ADMIN },
    ...routes,
  })
}

describe('Container detail', () => {
  it('shows the container, its stats and both charts', async () => {
    detail({
      'GET /containers/web?range=1h': {
        body: detailPayload({
          name: 'web',
          image: 'nginx:1',
          restart_count: 3,
          cpu_pct: 4,
          mem_used: 512 * MIB,
          mem_limit: GIB,
        }),
      },
    })
    renderApp('/containers/web')
    expect(await screen.findByText('Online')).toBeVisible()
    expect(screen.getByRole('heading', { level: 1, name: 'web' })).toBeVisible()
    expect(screen.getByText(/^nginx:1 · running · started 3 h ago$/)).toBeVisible()
    const stat = (label: string) => screen.getByText(label).closest('.vp-stat') as HTMLElement
    expect(within(stat('CPU')).getByText('4.0')).toBeVisible()
    expect(within(stat('Memory used')).getByText('512.0 MiB')).toBeVisible()
    expect(within(stat('Memory used')).getByText('of 1.0 GiB')).toBeVisible()
    expect(within(stat('Restarts')).getByText('3')).toBeVisible()
    expect(within(stat('Uptime')).getByText('3 hours')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'CPU, last hour' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Memory, last hour' })).toBeVisible()
    expect(screen.getByRole('img', { name: 'CPU, last hour' })).toBeInTheDocument()
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(within(crumbs).getByRole('link', { name: 'Containers' })).toHaveAttribute('href', '/containers')
  })

  it('says "no limit" for unlimited memory', async () => {
    detail({
      'GET /containers/web?range=1h': {
        body: detailPayload({ name: 'web', mem_limit: null }),
      },
    })
    renderApp('/containers/web')
    expect(await screen.findByText('no limit')).toBeVisible()
  })

  it('switches the range, reloads, and renames the charts', async () => {
    detail({
      'GET /containers/web?range=1h': { body: detailPayload({ name: 'web' }) },
      'GET /containers/web?range=6h': {
        body: detailPayload({ name: 'web' }, '6h'),
      },
    })
    renderApp('/containers/web')
    const tabs = await screen.findByRole('tablist', { name: 'Time range' })
    expect(within(tabs).getByRole('tab', { name: '1 h' })).toHaveAttribute('aria-selected', 'true')
    await userEvent.click(within(tabs).getByRole('tab', { name: '6 h' }))
    expect(await screen.findByRole('heading', { name: 'CPU, last 6 hours' })).toBeVisible()
    expect(within(tabs).getByRole('tab', { name: '6 h' })).toHaveAttribute('aria-selected', 'true')
  })

  it('moves between range tabs with the arrow keys', async () => {
    detail({
      'GET /containers/web?range=1h': { body: detailPayload({ name: 'web' }) },
      'GET /containers/web?range=6h': {
        body: detailPayload({ name: 'web' }, '6h'),
      },
    })
    renderApp('/containers/web')
    const first = await screen.findByRole('tab', { name: '1 h' })
    first.focus()
    await userEvent.keyboard('{ArrowRight}')
    expect(await screen.findByRole('heading', { name: 'CPU, last 6 hours' })).toBeVisible()
    expect(screen.getByRole('tab', { name: '6 h' })).toHaveFocus()
  })

  it('handles names with dots, dashes and underscores', async () => {
    const { fetchMock } = detail({
      'GET /containers/my-app_1.web?range=1h': {
        body: detailPayload({ name: 'my-app_1.web' }),
      },
    })
    renderApp('/containers/my-app_1.web')
    expect(await screen.findByRole('heading', { level: 1, name: 'my-app_1.web' })).toBeVisible()
    expect(fetchMock).toHaveBeenCalledWith('/api/containers/my-app_1.web?range=1h', expect.anything())
  })

  it('shows a not-found state for an unknown name', async () => {
    detail({
      'GET /containers/ghost?range=1h': {
        status: 404,
        body: { detail: 'No container named ghost' },
      },
    })
    renderApp('/containers/ghost')
    expect(await screen.findByText('No container named “ghost”')).toBeVisible()
    expect(screen.getByRole('link', { name: 'Back to containers' })).toHaveAttribute('href', '/containers')
  })

  it('says when the container is not running', async () => {
    detail({
      'GET /containers/web?range=1h': {
        body: detailPayload({
          name: 'web',
          state: 'exited',
          status: 'offline',
          cpu_pct: null,
          mem_used: null,
          mem_limit: null,
        }),
      },
    })
    renderApp('/containers/web')
    expect(await screen.findByText("This container isn't running.")).toBeVisible()
    expect(within(screen.getByText('Uptime').closest('.vp-stat') as HTMLElement).getByText('—')).toBeVisible()
  })

  it('says when the container was removed, and still shows its history', async () => {
    detail({
      'GET /containers/web?range=1h': {
        body: detailPayload({
          name: 'web',
          removed: true,
          status: 'offline',
          cpu_pct: null,
          mem_used: null,
          mem_limit: null,
        }),
      },
    })
    renderApp('/containers/web')
    expect(await screen.findByText('This container was removed from the host.')).toBeVisible()
    expect(screen.getByRole('img', { name: 'CPU, last hour' })).toBeInTheDocument()
  })

  it('shows empty charts, not an error, when there are no samples yet', async () => {
    const payload = detailPayload({ name: 'web' })
    payload.metrics.points = []
    detail({ 'GET /containers/web?range=1h': { body: payload } })
    renderApp('/containers/web')
    expect(await screen.findAllByText('No data for this range yet.')).toHaveLength(2)
  })

  it('shows a retryable error when the page cannot be loaded', async () => {
    let attempts = 0
    detail({
      'GET /containers/web?range=1h': () => {
        attempts += 1
        return attempts === 1 ? { networkError: true } : { body: detailPayload({ name: 'web' }) }
      },
    })
    renderApp('/containers/web')
    expect(await screen.findByText("Couldn't load this container")).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'web' })).toBeVisible()
  })

  it('does not call a removed container running', async () => {
    detail({
      'GET /containers/web?range=1h': {
        body: detailPayload({
          name: 'web',
          state: 'running',
          removed: true,
          status: 'offline',
          cpu_pct: null,
          mem_used: null,
          mem_limit: null,
        }),
      },
    })
    renderApp('/containers/web')
    expect(await screen.findByText('This container was removed from the host.')).toBeVisible()
    expect(screen.getByText(/^nginx:1 · removed · started /)).toBeVisible()
    expect(screen.queryByText(/running/)).not.toBeInTheDocument()
    expect(within(screen.getByText('Uptime').closest('.vp-stat') as HTMLElement).getByText('—')).toBeVisible()
  })
})
