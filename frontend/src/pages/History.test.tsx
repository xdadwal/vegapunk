import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ADMIN, renderApp, stubApi } from '../test-utils'
import type { Conversation } from '../types'

const id = (n: number) => String(n).repeat(32)
const ITEMS: Conversation[] = [
  {
    id: id(1),
    title: 'Why is pihole restarting?',
    updated_at: '2026-01-02T15:12:00Z',
    finding_id: null,
    finding_title: null,
  },
  {
    id: id(2),
    title: 'Free up space on tank',
    updated_at: '2026-01-01T09:05:00Z',
    finding_id: null,
    finding_title: null,
  },
  {
    id: id(3),
    title: 'Is anything wrong right now?',
    updated_at: '2025-12-30T00:30:00Z',
    finding_id: null,
    finding_title: null,
  },
]

function historyPage(
  items: Conversation[] | (() => Conversation[]) = ITEMS,
  extra: Parameters<typeof stubApi>[0] = {},
) {
  const api = stubApi({
    'GET /setup/status': { body: { admin_created: true } },
    'GET /me': { body: ADMIN },
    'GET /agent/conversations':
      typeof items === 'function' ? () => ({ body: { items: items() } }) : { body: { items } },
    ...extra,
  })
  renderApp('/agent/history')
  return api
}

const conversations = () => screen.findByRole('list', { name: 'Conversations' })
const items = () => within(screen.getByRole('list', { name: 'Conversations' })).getAllByRole('listitem')
const rows = () => {
  const list = screen.queryByRole('list', { name: 'Conversations' })
  return list
    ? within(list)
        .queryAllByRole('listitem')
        .map((r) => within(r).getByRole('link').textContent)
    : []
}

describe('History: conversations about a finding', () => {
  it('says which finding a conversation is about, as text, keeping the row link', async () => {
    historyPage([{ ...ITEMS[0], finding_id: 7, finding_title: 'web keeps <b>restarting</b>' }, ITEMS[1]])
    await conversations()
    const [linked, plain] = items()
    expect(within(linked).getByText('About finding: web keeps <b>restarting</b>')).toHaveClass('vp-service-meta')
    expect(within(linked).getAllByRole('link')).toHaveLength(1)
    expect(within(linked).getByRole('link')).toHaveAttribute('href', `/agent?c=${id(1)}`)
    expect(within(plain).queryByText(/About finding/)).not.toBeInTheDocument()
  })

  it('names no title when the finding has none', async () => {
    historyPage([{ ...ITEMS[0], finding_id: 7, finding_title: null }])
    await conversations()
    expect(within(items()[0]).getByText('About a finding')).toBeVisible()
  })
})

describe('History', () => {
  it('lists conversations in the order given, as links to the agent page, with their time', async () => {
    historyPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'History' })).toBeVisible()
    expect(screen.getByText('Past conversations with the agent, newest first')).toBeVisible()
    await conversations()
    expect(items()).toHaveLength(3)
    expect(rows()).toEqual(['Why is pihole restarting?', 'Free up space on tank', 'Is anything wrong right now?'])
    expect(screen.getByRole('link', { name: 'Free up space on tank' })).toHaveAttribute('href', `/agent?c=${id(2)}`)
    // the day as well as the time: a conversation from last week must not read as today
    expect(screen.getByText('Jan 2, 3:12 PM UTC')).toBeVisible()
    expect(screen.getByText('Dec 30, 12:30 AM UTC')).toBeVisible()
  })

  it('says when the agent is still answering in a conversation', async () => {
    historyPage([{ ...ITEMS[0], answering: true }, ITEMS[1]])
    await conversations()
    const [busy, idle] = items()
    expect(within(busy).getByText('Answering…')).toBeVisible()
    expect(within(busy).getByText(/Jan 2, 3:12 PM UTC/)).toBeVisible()
    expect(within(idle).queryByText('Answering…')).not.toBeInTheDocument()
  })

  it('has a breadcrumb back to the Agent page', async () => {
    historyPage()
    const crumbs = await screen.findByRole('navigation', { name: 'Breadcrumb' })
    expect(within(crumbs).getByRole('link', { name: 'Agent' })).toHaveAttribute('href', '/agent')
    expect(within(crumbs).getByText('History')).toHaveAttribute('aria-current', 'page')
  })

  it('filters by title and shows a no-match state with Reset', async () => {
    historyPage()
    const user = userEvent.setup()
    await conversations()
    await user.type(screen.getByLabelText('Search conversations'), 'TANK')
    expect(rows()).toEqual(['Free up space on tank'])
    await user.clear(screen.getByLabelText('Search conversations'))
    await user.type(screen.getByLabelText('Search conversations'), 'zzz')
    expect(screen.getByText('No conversations match “zzz”')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Reset' }))
    expect(rows()).toHaveLength(3)
    expect(screen.getByLabelText('Search conversations')).toHaveValue('')
  })

  it('treats the search text literally', async () => {
    historyPage([
      { id: id(1), title: 'a.b', updated_at: '2026-01-01T00:00:00Z', finding_id: null, finding_title: null },
      { id: id(2), title: 'axb', updated_at: '2026-01-01T00:00:00Z', finding_id: null, finding_title: null },
    ])
    await userEvent.type(await screen.findByLabelText('Search conversations'), 'a.b')
    expect(rows()).toEqual(['a.b'])
  })

  it('shows titles as plain text', async () => {
    historyPage([
      {
        id: id(1),
        title: '<b>bold</b> <img src=x onerror=alert(1)>',
        updated_at: '2026-01-01T00:00:00Z',
        finding_id: null,
        finding_title: null,
      },
    ])
    const link = await screen.findByRole('link', { name: /bold/ })
    expect(link.textContent).toBe('<b>bold</b> <img src=x onerror=alert(1)>')
    expect(document.querySelector('img')).toBeNull()
    expect(document.querySelector('b')).toBeNull()
  })

  it('deletes only after the confirmation step, and removes the row', async () => {
    let items = [...ITEMS]
    const { sent } = historyPage(() => items, {
      [`DELETE /agent/conversations/${id(2)}`]: () => {
        items = items.filter((c) => c.id !== id(2))
        return { status: 204 }
      },
    })
    const user = userEvent.setup()
    const row = (await screen.findByRole('link', { name: 'Free up space on tank' })).closest('li')!
    await user.click(within(row).getByRole('button', { name: 'Delete' }))
    expect(sent(`DELETE /agent/conversations/${id(2)}`)).toHaveLength(0)
    await user.click(within(row).getByRole('button', { name: 'Confirm delete' }))
    await vi.waitFor(() => expect(rows()).toEqual(['Why is pihole restarting?', 'Is anything wrong right now?']))
    expect(sent(`DELETE /agent/conversations/${id(2)}`)).toHaveLength(1)
  })

  it('lets only one row be in the confirm state at a time', async () => {
    historyPage()
    const user = userEvent.setup()
    await conversations()
    const [first, second] = items()
    await user.click(within(first).getByRole('button', { name: 'Delete' }))
    await user.click(within(second).getByRole('button', { name: 'Delete' }))
    expect(within(first).getByRole('button', { name: 'Delete' })).toBeVisible()
    expect(within(second).getByRole('button', { name: 'Confirm delete' })).toBeVisible()
  })

  it('cancels the confirmation after five seconds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      historyPage()
      const delete1 = (await screen.findAllByRole('button', { name: 'Delete' }))[0]
      await act(async () => {
        delete1.click()
      })
      expect(screen.getByRole('button', { name: 'Confirm delete' })).toBeVisible()
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_100)
      })
      expect(screen.queryByRole('button', { name: 'Confirm delete' })).not.toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps the row and says why when a delete fails', async () => {
    historyPage(ITEMS, {
      [`DELETE /agent/conversations/${id(1)}`]: { status: 500, body: { detail: 'Request failed (500)' } },
    })
    const user = userEvent.setup()
    await user.click((await screen.findAllByRole('button', { name: 'Delete' }))[0])
    await user.click(screen.getByRole('button', { name: 'Confirm delete' }))
    expect(await screen.findByText('Request failed (500)')).toBeVisible()
    expect(rows()).toHaveLength(3)
  })

  it('says the agent is busy when delete returns 409', async () => {
    historyPage(ITEMS, {
      [`DELETE /agent/conversations/${id(1)}`]: {
        status: 409,
        body: { detail: 'The agent is answering. Try again when it has finished.', code: 'busy' },
      },
    })
    const user = userEvent.setup()
    await user.click((await screen.findAllByRole('button', { name: 'Delete' }))[0])
    await user.click(screen.getByRole('button', { name: 'Confirm delete' }))
    expect(await screen.findByText('The agent is answering. Try again when it has finished.')).toBeVisible()
    expect(rows()).toHaveLength(3)
  })

  it('treats a conversation that is already gone as deleted', async () => {
    let items = [...ITEMS]
    historyPage(() => items, {
      [`DELETE /agent/conversations/${id(1)}`]: () => {
        items = items.slice(1) // deleted in another tab
        return { status: 404, body: { detail: 'That conversation no longer exists.' } }
      },
    })
    const user = userEvent.setup()
    await user.click((await screen.findAllByRole('button', { name: 'Delete' }))[0])
    await user.click(screen.getByRole('button', { name: 'Confirm delete' }))
    await vi.waitFor(() => expect(rows()).toHaveLength(2))
  })

  it('shows the empty state with a way to start', async () => {
    historyPage([])
    expect(await screen.findByText('No conversations yet')).toBeVisible()
    expect(screen.getByRole('link', { name: 'Ask the agent' })).toHaveAttribute('href', '/agent')
    expect(screen.queryByLabelText('Search conversations')).not.toBeInTheDocument()
  })

  it('shows a retryable error when the list cannot be loaded', async () => {
    let attempts = 0
    historyPage(() => ITEMS, {
      'GET /agent/conversations': () => {
        attempts += 1
        return attempts === 1 ? { networkError: true } : { body: { items: ITEMS } }
      },
    })
    expect(await screen.findByText("Couldn't load conversations")).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await conversations()
    expect(items()).toHaveLength(3)
  })

  it('keeps Agent marked in the navigation', async () => {
    historyPage()
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    expect(within(nav).getByRole('link', { name: 'Agent' })).toHaveAttribute('aria-current', 'page')
  })
})
