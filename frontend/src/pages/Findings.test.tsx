import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { findingItem, findingList } from '../test-fixtures'
import { ADMIN, renderApp, stubApi } from '../test-utils'
import type { FindingList } from '../types'

const OPEN = findingItem({ id: 'a', title: 'web keeps restarting', subject: 'web' })
const RESOLVED = findingItem({ id: 'b', title: 'Disk was filling', subject: 'host', status: 'resolved' })
const REVIEWED = findingItem({ id: 'c', title: 'db was killed', subject: 'db', status: 'reviewed' })

function show(list: FindingList, extra: Parameters<typeof stubApi>[0] = {}) {
  return stubApi({
    'GET /setup/status': { body: { admin_created: true } },
    'GET /me': { body: ADMIN },
    'GET /findings': { body: list },
    'GET /findings?status=open': { body: { ...list, items: list.items.filter((f) => f.status === 'open') } },
    ...extra,
  })
}

const titles = () => screen.queryAllByRole('row').map((r) => within(r).getByRole('link').textContent)

describe('Findings', () => {
  it('links each row through its title', async () => {
    show(findingList([OPEN]))
    renderApp('/findings')
    const link = await screen.findByRole('link', { name: 'web keeps restarting' })
    expect(link).toHaveClass('vp-card-link')
    expect(link).toHaveAttribute('href', '/findings/a')
    expect(screen.getByRole('row')).toHaveClass('vp-service-linked')
  })

  it('names each status and shows the subject and time found', async () => {
    show(findingList([OPEN, RESOLVED, REVIEWED]))
    renderApp('/findings')
    const rows = await screen.findAllByRole('row')
    expect(within(rows[0]).getByText('Needs you')).toBeVisible()
    expect(within(rows[1]).getByText('Resolved')).toBeVisible()
    expect(within(rows[2]).getByText('Reviewed')).toBeVisible()
    expect(within(rows[1]).getByText('host')).toHaveClass('vp-mono')
    expect(within(rows[0]).getByText('Found 3:00 PM UTC')).toBeVisible()
  })

  it('keeps the order the server sends', async () => {
    show(findingList([OPEN, REVIEWED, RESOLVED]))
    renderApp('/findings')
    await screen.findAllByRole('row')
    expect(titles()).toEqual(['web keeps restarting', 'db was killed', 'Disk was filling'])
  })

  it('filters by text and status, and resets', async () => {
    show(findingList([OPEN, RESOLVED, REVIEWED]))
    renderApp('/findings')
    const user = userEvent.setup()
    await screen.findAllByRole('row')
    await user.type(screen.getByLabelText('Search findings'), 'DISK')
    expect(titles()).toEqual(['Disk was filling'])
    await user.clear(screen.getByLabelText('Search findings'))
    await user.selectOptions(screen.getByLabelText('Status'), 'Reviewed')
    expect(titles()).toEqual(['db was killed'])
    await user.type(screen.getByLabelText('Search findings'), 'zzz')
    expect(screen.getByText('No findings match')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Reset' }))
    expect(titles()).toHaveLength(3)
    expect(screen.getByLabelText('Status')).toHaveValue('any')
  })

  it('pages ten at a time', async () => {
    const many = Array.from({ length: 12 }, (_, i) => findingItem({ id: `x${i}`, title: `Finding ${i}` }))
    show(findingList(many))
    renderApp('/findings')
    const user = userEvent.setup()
    await screen.findAllByRole('row')
    expect(titles()).toHaveLength(10)
    expect(screen.getByText('12 findings match')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Next page' }))
    expect(titles()).toEqual(['Finding 10', 'Finding 11'])
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Previous page' }))
    expect(titles()).toHaveLength(10)
  })

  it('says there are none yet, and when the next check is due', async () => {
    show(findingList([]))
    renderApp('/findings')
    expect(await screen.findByText('No findings yet')).toBeVisible()
    expect(
      screen.getByText(
        'The agent checks once a day at 2:00 AM UTC and watches for restarts, memory kills, high use and filling storage.',
      ),
    ).toBeVisible()
  })

  it('says daily checks are off when they are', async () => {
    show(findingList([], { enabled: false }))
    renderApp('/findings')
    expect(await screen.findByText('Daily checks are off. Turn them on in Settings.')).toBeVisible()
  })

  it('names the last scheduled check in the header', async () => {
    show(findingList([OPEN], { last_run_at: '2026-11-27T02:00:00Z' }))
    renderApp('/findings')
    expect(
      await screen.findByText(
        'What the agent noticed in container logs, metrics and storage · last scheduled check 2:00 AM UTC',
      ),
    ).toBeVisible()
  })

  it('shows alerts for an agent that is off, a rejected key, and unsupported models', async () => {
    show(findingList([OPEN], { skipped: 'off' }))
    const { unmount } = renderApp('/findings')
    expect(await screen.findByText('The agent is off')).toBeVisible()
    expect(screen.getByRole('link', { name: 'Settings' })).toHaveAttribute('href', '/settings')
    unmount()

    show(findingList([OPEN], { skipped: 'key_rejected' }))
    const second = renderApp('/findings')
    expect(await screen.findByText('Your AI provider rejected the API key')).toBeVisible()
    second.unmount()

    show(findingList([OPEN], { skipped: 'unsupported' }))
    renderApp('/findings')
    expect(
      await screen.findByText(
        "Your AI provider can't give structured answers, so checks can't run. Try another model.",
      ),
    ).toBeVisible()
  })

  it('shows the last check failure', async () => {
    show(findingList([OPEN], { last_error: 'boom' }))
    renderApp('/findings')
    expect(await screen.findByText('The last check failed: boom')).toBeVisible()
  })

  it('starts a check and shows the refusal when the server says no', async () => {
    show(findingList([OPEN]), {
      'POST /findings/check': { status: 409, body: { detail: 'Connect an AI provider first.' } },
    })
    renderApp('/findings')
    await userEvent.click(await screen.findByRole('button', { name: 'Run check now' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Connect an AI provider first.')
  })

  it('posts the check and refetches', async () => {
    const { sent } = show(findingList([OPEN]), { 'POST /findings/check': { status: 202, body: { started: true } } })
    renderApp('/findings')
    await userEvent.click(await screen.findByRole('button', { name: 'Run check now' }))
    expect(sent('POST /findings/check')).toHaveLength(1)
  })

  it('disables Run check now while a check is running', async () => {
    show(findingList([OPEN], { running: true }))
    renderApp('/findings')
    expect(await screen.findByRole('button', { name: 'Run check now' })).toBeDisabled()
  })

  it('renders titles as text', async () => {
    const title = '<img src=x onerror=alert(1)>'
    show(findingList([findingItem({ title })]))
    const { container } = renderApp('/findings')
    expect(await screen.findByRole('link', { name: title })).toBeVisible()
    expect(container.querySelector('img')).toBeNull()
  })

  it('shows a retryable error when the list cannot be loaded', async () => {
    let attempts = 0
    show(findingList([OPEN]), {
      'GET /findings': () => {
        attempts += 1
        return attempts === 1 ? { networkError: true } : { body: findingList([OPEN]) }
      },
    })
    renderApp('/findings')
    expect(await screen.findByText("Couldn't load findings")).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('link', { name: 'web keeps restarting' })).toBeVisible()
  })

  it('shows a malformed timestamp as it came instead of crashing', async () => {
    show(findingList([findingItem({ first_seen_at: 'not a date' })], { last_run_at: 'also bad' }))
    renderApp('/findings')
    expect(await screen.findByText('Found not a date')).toBeVisible()
    expect(screen.getByText(/last scheduled check also bad/)).toBeVisible()
  })
})
