import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { containerItem, hostPayload } from '../test-fixtures'
import { ADMIN, renderApp, stubApi } from '../test-utils'

const MIB = 1024 ** 2

const WEB = containerItem({
  id: '1',
  name: 'web',
  image: 'nginx:1',
  cpu_pct: 5,
  mem_used: 512 * MIB,
  mem_limit: 1024 * MIB,
})
const DB = containerItem({
  id: '2',
  name: 'db',
  image: 'postgres:16',
  state: 'exited',
  status: 'offline',
  cpu_pct: null,
  mem_used: null,
  mem_limit: null,
})
const CACHE = containerItem({
  id: '3',
  name: 'cache',
  image: 'redis:7',
  state: 'restarting',
  status: 'degraded',
  restart_count: 2,
  cpu_pct: null,
  mem_used: null,
  mem_limit: null,
})

function list(items = [DB, CACHE, WEB], extra: Parameters<typeof stubApi>[0] = {}) {
  return stubApi({
    'GET /setup/status': { body: { admin_created: true } },
    'GET /me': { body: ADMIN },
    'GET /host': { body: hostPayload() },
    'GET /host/metrics?range=1h': {
      body: { range: '1h', resolution_seconds: 15, points: [] },
    },
    'GET /containers': { body: { total: items.length, items } },
    ...extra,
  })
}

describe('Containers', () => {
  it('lists containers in the order the server sends, with counts above', async () => {
    list()
    renderApp('/containers')
    expect(await screen.findByText('3 Docker containers · 1 running')).toBeVisible()
    const rows = screen.getAllByRole('row')
    expect(rows.map((r) => within(r).getByRole('link').textContent)).toEqual(['db', 'cache', 'web'])
    expect(within(rows[0]).getByText('Offline')).toBeVisible()
    expect(within(rows[1]).getByText('Degraded')).toBeVisible()
    expect(within(rows[2]).getByText('Online')).toBeVisible()
  })

  it('uses the singular for one container', async () => {
    list([WEB])
    renderApp('/containers')
    expect(await screen.findByText('1 Docker container · 1 running')).toBeVisible()
  })

  it('shows CPU, memory and restarts on each row', async () => {
    list([
      containerItem({ ...WEB, restart_count: 2 }),
      containerItem({
        id: '9',
        name: 'one',
        restart_count: 1,
        cpu_pct: 2,
        mem_used: 100 * MIB,
        mem_limit: null,
      }),
    ])
    renderApp('/containers')
    expect(await screen.findByText('CPU 5.0% · Memory 512.0 MiB of 1.0 GiB · 2 restarts')).toBeVisible()
    expect(screen.getByText('CPU 2.0% · Memory 100.0 MiB, no limit · 1 restart')).toBeVisible()
  })

  it('shows dashes for a container with no sample', async () => {
    list([DB])
    renderApp('/containers')
    expect(await screen.findByText('CPU — · Memory —')).toBeVisible()
  })

  it('shows the image', async () => {
    list([WEB])
    renderApp('/containers')
    expect(await screen.findByText('nginx:1')).toBeVisible()
  })

  it('filters by text on name or image, and by status, and resets', async () => {
    list()
    renderApp('/containers')
    const user = userEvent.setup()
    await screen.findByText('3 Docker containers · 1 running')
    const names = () => screen.queryAllByRole('row').map((r) => within(r).getByRole('link').textContent)

    await user.type(screen.getByLabelText('Search containers'), 'REDIS')
    expect(names()).toEqual(['cache'])
    await user.clear(screen.getByLabelText('Search containers'))
    await user.type(screen.getByLabelText('Search containers'), 'we')
    expect(names()).toEqual(['web'])

    await user.clear(screen.getByLabelText('Search containers'))
    await user.selectOptions(screen.getByLabelText('Status'), 'Exited')
    expect(names()).toEqual(['db'])

    await user.type(screen.getByLabelText('Search containers'), 'zzz')
    expect(screen.getByText('No containers match “zzz”')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Reset' }))
    expect(names()).toEqual(['db', 'cache', 'web'])
    expect(screen.getByLabelText('Status')).toHaveValue('any')
  })

  it('treats the search text literally', async () => {
    list([containerItem({ name: 'a.b' }), containerItem({ id: '2', name: 'axb' })])
    renderApp('/containers')
    await userEvent.type(await screen.findByLabelText('Search containers'), 'a.b')
    expect(screen.getAllByRole('row')).toHaveLength(1)
  })

  it('keeps the header counts unfiltered while filtering', async () => {
    list()
    renderApp('/containers')
    await userEvent.type(await screen.findByLabelText('Search containers'), 'web')
    expect(screen.getByText('3 Docker containers · 1 running')).toBeVisible()
  })

  it('shows the empty message when Docker reports nothing', async () => {
    list([])
    renderApp('/containers')
    expect(await screen.findByText('No containers')).toBeVisible()
    expect(screen.getByText('Docker reports no containers on this host.')).toBeVisible()
  })

  it('shows the Docker error above the list and keeps the stored list', async () => {
    const host = hostPayload()
    host.capture.sources.docker = {
      ok: false,
      stale: false,
      last_success_at: null,
      error: "Can't reach Docker at unix:///x.",
    }
    list([WEB], { 'GET /host': { body: host } })
    renderApp('/containers')
    expect(await screen.findByText("Can't reach Docker")).toBeVisible()
    expect(screen.getByRole('link', { name: 'web' })).toBeVisible()
  })

  it('links each row to its detail page', async () => {
    list([containerItem({ name: 'my-app_1.web' })])
    renderApp('/containers')
    expect(await screen.findByRole('link', { name: 'my-app_1.web' })).toHaveAttribute(
      'href',
      '/containers/my-app_1.web',
    )
  })

  it('adds Containers to the navigation and keeps it marked on a detail page', async () => {
    list()
    renderApp('/containers')
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: 'Containers' })).toHaveAttribute('aria-current', 'page')
  })

  it('shows a retryable error when the list cannot be loaded', async () => {
    let attempts = 0
    list([WEB], {
      'GET /containers': () => {
        attempts += 1
        return attempts === 1 ? { networkError: true } : { body: { total: 1, items: [WEB] } }
      },
    })
    renderApp('/containers')
    expect(await screen.findByText("Couldn't load containers")).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('link', { name: 'web' })).toBeVisible()
  })
})
