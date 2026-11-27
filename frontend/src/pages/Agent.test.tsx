import { act, fireEvent, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { CONFIGURED, aiSettings, findingDetail } from '../test-fixtures'
import { ADMIN, renderApp, stubApi } from '../test-utils'
import type { AiSettings, ChatEvent } from '../types'

const CID = 'c'.repeat(32)
const line = (event: ChatEvent) => JSON.stringify(event) + '\n'
const enc = new TextEncoder()
// Stop ignores clicks right after Send (a double-click must not stop the answer)
const later = () => new Promise((resolve) => setTimeout(resolve, 550))

const ANSWER: ChatEvent[] = [
  { type: 'conversation', id: CID, title: 'Is anything wrong?', finding_id: null },
  { type: 'step', id: 's1', label: 'Read the status of the containers', status: 'running' },
  { type: 'step', id: 's1', label: 'Read the status of the containers', status: 'done' },
  { type: 'text', text: '`db` is ' },
  { type: 'text', text: 'stopped.' },
  { type: 'done', usage: { input_tokens: 10, output_tokens: 5 } },
]

type Options = {
  settings?: AiSettings | (() => AiSettings)
  chat?: Parameters<typeof stubApi>[0][string]
  conversation?: Parameters<typeof stubApi>[0][string]
  extra?: Parameters<typeof stubApi>[0]
}

function agentPage({ settings = CONFIGURED, chat, conversation, extra }: Options = {}, path = '/agent') {
  const routes: Parameters<typeof stubApi>[0] = {
    'GET /setup/status': { body: { admin_created: true } },
    'GET /me': { body: ADMIN },
    'GET /settings/ai': typeof settings === 'function' ? () => ({ body: settings() }) : { body: settings },
    'POST /agent/chat': chat ?? { stream: ANSWER.map(line) },
    ...extra,
  }
  if (conversation) routes[`GET /agent/conversations/${CID}`] = conversation
  const api = stubApi(routes)
  const view = renderApp(path, { probe: true })
  return { ...api, view }
}

const composer = () => screen.findByLabelText('Message the agent')

async function ask(text = 'Is anything wrong?') {
  const user = userEvent.setup()
  await user.type(await composer(), text)
  await user.click(screen.getByRole('button', { name: 'Send' }))
  return user
}

function chatCall(fetchMock: ReturnType<typeof stubApi>['fetchMock']) {
  return fetchMock.mock.calls.find(([url]) => url === '/api/agent/chat') as unknown as [string, RequestInit] | undefined
}

describe('Agent page', () => {
  it('shows the header, the read-only note and the empty thread prompt', async () => {
    agentPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Agent' })).toBeVisible()
    expect(
      screen.getByText("Reads this host's containers, logs and metrics and answers questions · times in UTC"),
    ).toBeVisible()
    expect(await screen.findByText("The agent only reads. It can't start, stop or change anything.")).toBeVisible()
    expect(await screen.findByText('Ask about this host')).toBeVisible()
    expect(
      screen.getByText('For example: “Is anything wrong right now?” or “Why is a container restarting?”'),
    ).toBeVisible()
    expect(await composer()).toHaveAccessibleDescription('Enter sends. Shift+Enter adds a line.')
    expect(screen.getByRole('link', { name: 'History' })).toHaveAttribute('href', '/agent/history')
    expect(screen.getByRole('link', { name: 'History' })).toHaveClass('vp-btn-quiet') // the design's secondary header link
  })

  it('sends a message, shows it, then streams the answer with its steps', async () => {
    const { sent } = agentPage()
    await ask()
    expect(await screen.findByRole('article', { name: 'You' })).toHaveTextContent('Is anything wrong?')
    const answer = await screen.findByRole('article', { name: 'Agent' })
    await vi.waitFor(() => expect(answer).toHaveTextContent('`db` is stopped.'.replace(/`/g, '')))
    const steps = within(answer).getByRole('list', { name: 'Steps' })
    expect(steps).toHaveTextContent('Done: Read the status of the containers')
    expect(within(answer).queryByText('Thinking…')).not.toBeInTheDocument()
    expect(answer).not.toHaveAttribute('aria-busy')
    expect(sent('POST /agent/chat')).toEqual([{ message: 'Is anything wrong?', conversation_id: null }])
    expect(await composer()).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
  })

  it('shows Thinking…, then the text as it streams, with steps as they happen', async () => {
    let stream!: ReadableStreamDefaultController<Uint8Array>
    agentPage({ chat: { stream: (c) => void (stream = c) } })
    await ask()
    const answer = await screen.findByRole('article', { name: 'Agent' })
    expect(within(answer).getByText('Thinking…')).toBeVisible()
    expect(answer).toHaveAttribute('aria-busy', 'true')

    await act(async () => {
      stream.enqueue(enc.encode(line({ type: 'conversation', id: CID, title: 't', finding_id: null })))
      stream.enqueue(enc.encode(line({ type: 'step', id: 's', label: 'Read the host', status: 'running' })))
    })
    expect(within(answer).getByText(/Running:/)).toBeVisible()
    expect(within(answer).getByText('Thinking…')).toBeVisible() // steps alone are not an answer

    await act(async () => stream.enqueue(enc.encode(line({ type: 'text', text: 'Partly' }))))
    expect(within(answer).queryByText('Thinking…')).not.toBeInTheDocument()
    expect(answer).toHaveTextContent('Partly')
    expect(answer.querySelector('.vp-caret')).not.toBeNull()

    await act(async () => {
      stream.enqueue(enc.encode(line({ type: 'done', usage: { input_tokens: 1, output_tokens: 1 } })))
      stream.close()
    })
    expect(answer.querySelector('.vp-caret')).toBeNull()
  })

  it("writes the new conversation's id into the URL", async () => {
    agentPage()
    await ask()
    await vi.waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(`/agent?c=${CID}`))
  })

  it('continues the same conversation with the next message', async () => {
    const { sent } = agentPage()
    const user = await ask('one')
    await screen.findByRole('button', { name: 'Send' })
    await user.type(await composer(), 'two')
    await user.click(screen.getByRole('button', { name: 'Send' }))
    await vi.waitFor(() => expect(sent('POST /agent/chat')).toHaveLength(2))
    expect(sent('POST /agent/chat')[1]).toEqual({ message: 'two', conversation_id: CID })
  })

  it('Enter sends and Shift+Enter adds a line', async () => {
    const { sent } = agentPage()
    const user = userEvent.setup()
    await user.type(await composer(), 'one{Shift>}{Enter}{/Shift}two{Enter}')
    await vi.waitFor(() => expect(sent('POST /agent/chat')).toHaveLength(1))
    expect(sent('POST /agent/chat')[0]).toMatchObject({ message: 'one\ntwo' })
  })

  it('refuses an empty or whitespace-only message without calling the API', async () => {
    const { sent } = agentPage()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Send' }))
    await user.type(await composer(), '   {Enter}')
    expect(sent('POST /agent/chat')).toHaveLength(0)
  })

  it('refuses an over-long message with a clear message, and keeps what was typed', async () => {
    const { sent } = agentPage()
    const box = await composer()
    fireEvent.change(box, { target: { value: 'x'.repeat(4_001) } })
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(box).toHaveAccessibleDescription('Messages can be up to 4,000 characters.')
    expect(box).toHaveValue('x'.repeat(4_001))
    expect(sent('POST /agent/chat')).toHaveLength(0)
    fireEvent.change(box, { target: { value: 'x'.repeat(4_000) } })
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    await vi.waitFor(() => expect(sent('POST /agent/chat')).toHaveLength(1))
  })

  it('counts characters, not UTF-16 units, for the limit', async () => {
    const { sent } = agentPage()
    fireEvent.change(await composer(), { target: { value: '\u{1F600}'.repeat(3_000) } })
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    await vi.waitFor(() => expect(sent('POST /agent/chat')).toHaveLength(1))
  })

  it('turns Send into Stop and stops the answer, keeping the partial text', async () => {
    let stream!: ReadableStreamDefaultController<Uint8Array>
    const { fetchMock } = agentPage({ chat: { stream: (c) => void (stream = c) } })
    const user = await ask()
    await act(async () => {
      stream.enqueue(enc.encode(line({ type: 'conversation', id: CID, title: 't', finding_id: null })))
      stream.enqueue(enc.encode(line({ type: 'text', text: 'Part of an answer' })))
    })
    expect(await screen.findByText('Part of an answer')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Send' })).not.toBeInTheDocument()
    await later()
    await user.click(screen.getByRole('button', { name: 'Stop' }))
    expect(chatCall(fetchMock)![1].signal!.aborted).toBe(true)
    expect(await screen.findByRole('button', { name: 'Send' })).toBeEnabled()
    expect(screen.getByText('Part of an answer')).toBeVisible()
    expect(screen.getByRole('article', { name: 'Agent' })).not.toHaveAttribute('aria-busy')
    expect(await composer()).toBeEnabled()
  })

  it('says Stopped. when there was nothing to keep', async () => {
    agentPage({ chat: { stream: () => {} } })
    const user = await ask()
    await screen.findByRole('button', { name: 'Stop' })
    await later()
    await user.click(screen.getByRole('button', { name: 'Stop' }))
    expect(await screen.findByText('Stopped.')).toBeVisible()
  })

  it('does not start a second answer while one is being written', async () => {
    const { sent } = agentPage({ chat: { stream: () => {} } })
    const user = await ask('first')
    await screen.findByRole('button', { name: 'Stop' })
    await user.type(await composer(), 'second{Enter}')
    expect(sent('POST /agent/chat')).toHaveLength(1)
  })

  it('keeps the composer and thread usable after an error event', async () => {
    const { sent } = agentPage({
      chat: {
        stream: [
          line({ type: 'conversation', id: CID, title: 't', finding_id: null }),
          line({ type: 'error', code: 'timeout', message: 'The agent took too long to answer.' }),
        ],
      },
    })
    const user = await ask('first')
    expect(await screen.findByText('The agent took too long to answer.')).toBeVisible()
    expect(await composer()).toBeEnabled()
    await user.type(await composer(), 'again{Enter}')
    await vi.waitFor(() => expect(sent('POST /agent/chat')).toHaveLength(2))
  })
})

describe('Agent page: key rejected', () => {
  it('shows the banner and disables the composer after the error event', async () => {
    let rejected = false
    agentPage({
      settings: () => (rejected ? { ...CONFIGURED, key_rejected_at: '2026-01-01T15:12:00Z' } : CONFIGURED),
      chat: () => {
        rejected = true // the provider rejects the key as the answer is asked for
        return {
          stream: [
            line({ type: 'conversation', id: CID, title: 't', finding_id: null }),
            line({ type: 'error', code: 'key_rejected', message: 'Your AI provider rejected the API key.' }),
          ],
        }
      },
    })
    await ask()
    // the persistent banner (its title has no full stop; the answer's own message does)
    const banner = (await screen.findByText('Your AI provider rejected the API key')).closest('.vp-alert')!
    await vi.waitFor(() =>
      expect(banner).toHaveTextContent('Chat is paused since 3:12 PM UTC. Save a working key in Settings.'),
    )
    expect(screen.getByText('Your AI provider rejected the API key.')).toBeVisible() // under the answer
    expect(await composer()).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })

  it('shows the banner on arrival when settings say so', async () => {
    agentPage({ settings: { ...CONFIGURED, key_rejected_at: '2026-01-01T15:12:00Z' } })
    expect(await screen.findByText('Your AI provider rejected the API key')).toBeVisible()
    expect(await composer()).toBeDisabled()
  })
})

describe('Agent page: AI off', () => {
  it('shows the AI-off state, with a way to Settings and no composer', async () => {
    agentPage({ settings: aiSettings() })
    expect(await screen.findByText('AI is off')).toBeVisible()
    expect(
      screen.getByText('Connect an AI provider to chat with the agent. Monitoring keeps working without it.'),
    ).toBeVisible()
    expect(screen.getByRole('link', { name: 'Open Settings' })).toHaveAttribute('href', '/settings')
    expect(screen.queryByLabelText('Message the agent')).not.toBeInTheDocument()
  })

  it('moves to the AI-off state when the server says so before streaming', async () => {
    let off = false
    agentPage({
      settings: () => (off ? aiSettings() : CONFIGURED),
      chat: () => {
        off = true
        return { status: 409, body: { detail: 'Connect an AI provider first.', code: 'ai_off' } }
      },
    })
    await ask()
    expect(await screen.findByText('AI is off', {}, { timeout: 3_000 })).toBeVisible()
  })
})

describe('Agent page: conversations', () => {
  const STORED = {
    id: CID,
    title: 'Is anything wrong?',
    updated_at: '2026-01-01T12:00:00Z',
    available: true,
    messages: [
      { role: 'user', text: 'Is anything wrong?', steps: [] },
      {
        role: 'assistant',
        text: '`db` is stopped.',
        steps: [{ label: 'Read the status of the containers', status: 'done' }],
      },
    ],
  }

  it('loads and shows a stored conversation from ?c=, and continues it', async () => {
    const { sent } = agentPage({ conversation: { body: STORED } }, `/agent?c=${CID}`)
    expect(await screen.findByRole('article', { name: 'You' })).toHaveTextContent('Is anything wrong?')
    expect(screen.getByRole('article', { name: 'Agent' })).toHaveTextContent('db is stopped.')
    expect(screen.getByRole('list', { name: 'Steps' })).toHaveTextContent('Done: Read the status of the containers')
    await userEvent.type(await composer(), 'And now?{Enter}')
    await vi.waitFor(() => expect(sent('POST /agent/chat')).toHaveLength(1))
    expect(sent('POST /agent/chat')[0]).toEqual({ message: 'And now?', conversation_id: CID })
  })

  it('says when a conversation is gone', async () => {
    agentPage(
      { conversation: { status: 404, body: { detail: 'That conversation no longer exists.' } } },
      `/agent?c=${CID}`,
    )
    expect(await screen.findByText('That conversation no longer exists.')).toBeVisible()
    expect(await composer()).toBeDisabled()
    expect(screen.getByRole('button', { name: 'New conversation' })).toBeVisible()
  })

  it('says when a conversation is no longer available, and starts a new one on request', async () => {
    agentPage({ conversation: { body: { ...STORED, available: false, messages: [] } } }, `/agent?c=${CID}`)
    expect(await screen.findByText('This conversation is no longer available. Start a new one.')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'New conversation' }))
    expect(await screen.findByText('Ask about this host')).toBeVisible()
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/agent$/)
    expect(await composer()).toBeEnabled()
  })

  it('shows a retryable error when the conversation cannot be loaded', async () => {
    let attempts = 0
    agentPage(
      {
        conversation: () => {
          attempts += 1
          return attempts === 1 ? { networkError: true } : { body: STORED }
        },
      },
      `/agent?c=${CID}`,
    )
    expect(await screen.findByText("Couldn't load this conversation")).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('article', { name: 'Agent' })).toBeVisible()
  })

  it('New conversation clears the thread and the URL', async () => {
    agentPage()
    const user = await ask()
    await screen.findByText(/stopped\./)
    await user.click(screen.getByRole('button', { name: 'New conversation' }))
    expect(await screen.findByText('Ask about this host')).toBeVisible()
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/agent$/)
  })

  it('does not reload a conversation it has just created', async () => {
    agentPage({ conversation: { body: { ...STORED, messages: [] } } })
    await ask()
    await screen.findByText(/stopped\./)
    await vi.waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(`?c=${CID}`))
    expect(screen.getByRole('article', { name: 'You' })).toBeVisible() // not wiped by a reload of an empty history
  })
})

describe('Agent page: failures before streaming', () => {
  it('handles busy: shows why, takes the message back out of the thread and restores it', async () => {
    agentPage({ chat: { status: 409, body: { detail: 'The agent is already answering.', code: 'busy' } } })
    await ask('my question')
    expect(await screen.findByText('The agent is already answering. Wait for it to finish.')).toBeVisible()
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    expect(await composer()).toHaveValue('my question')
  })

  it('handles a conversation that no longer exists', async () => {
    agentPage(
      {
        chat: { status: 404, body: { detail: 'That conversation no longer exists.' } },
        conversation: {
          body: {
            id: CID,
            title: 't',
            updated_at: '2026-01-01T12:00:00Z',
            available: true,
            messages: [{ role: 'user', text: 'q', steps: [] }],
          },
        },
      },
      `/agent?c=${CID}`,
    )
    await screen.findByRole('article', { name: 'You' })
    await ask('another')
    expect(await screen.findByText('That conversation no longer exists.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'New conversation' })).toBeVisible()
  })

  it('shows a network failure and keeps the text', async () => {
    agentPage({ chat: { networkError: true } })
    await ask('my question')
    expect(await screen.findByText("Couldn't reach the server.")).toBeVisible()
    expect(await composer()).toHaveValue('my question')
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
  })

  it('shows a connection lost mid-answer under the answer', async () => {
    agentPage({
      chat: {
        stream: (controller) => {
          controller.enqueue(enc.encode(line({ type: 'text', text: 'Half an answ' })))
          // the connection drops after the first bytes have arrived
          setTimeout(() => controller.error(new TypeError('network error')), 30)
        },
      },
    })
    await ask()
    expect(await screen.findByText('The connection was lost.')).toBeVisible()
    expect(screen.getByRole('article', { name: 'Agent' })).toHaveTextContent('Half an answ')
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
  })
})

describe('Agent page: safety and wiring', () => {
  it('shows model output as text, never as markup', async () => {
    agentPage({
      chat: {
        stream: [
          line({ type: 'conversation', id: CID, title: 't', finding_id: null }),
          line({ type: 'text', text: '<img src=x onerror=alert(1)> <script>alert(2)</script>' }),
          line({ type: 'done', usage: { input_tokens: 1, output_tokens: 1 } }),
        ],
      },
    })
    await ask()
    const answer = await screen.findByRole('article', { name: 'Agent' })
    await vi.waitFor(() => expect(answer).toHaveTextContent('<img src=x onerror=alert(1)>'))
    expect(document.querySelector('img')).toBeNull()
    expect(document.querySelector('script')).toBeNull()
  })

  it('shows what the person typed as text too', async () => {
    agentPage()
    await ask('<b>bold</b>')
    expect((await screen.findByRole('article', { name: 'You' })).querySelector('b')).toBeNull()
  })

  it('ends the stream when the page is left', async () => {
    const { fetchMock, view } = agentPage({ chat: { stream: () => {} } })
    await ask()
    await screen.findByRole('button', { name: 'Stop' })
    const signal = chatCall(fetchMock)![1].signal!
    expect(signal.aborted).toBe(false)
    view.unmount()
    expect(signal.aborted).toBe(true)
  })

  it('adds Agent to the navigation', async () => {
    agentPage()
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    const link = within(nav).getByRole('link', { name: 'Agent' })
    expect(link).toHaveAttribute('href', '/agent')
    expect(link).toHaveAttribute('aria-current', 'page')
  })
})

describe('Agent page: review fixes', () => {
  it('shows an empty thread when the Agent link is used while a conversation is open', async () => {
    const stored = {
      id: CID,
      title: 'Old',
      updated_at: '2026-01-01T12:00:00Z',
      available: true,
      messages: [{ role: 'user', text: 'old question', steps: [] }],
    }
    const { sent } = agentPage({ conversation: { body: stored } }, `/agent?c=${CID}`)
    expect(await screen.findByText('old question')).toBeVisible()
    const nav = await screen.findByRole('navigation', { name: 'Main' })
    await userEvent.click(within(nav).getByRole('link', { name: 'Agent' }))
    expect(await screen.findByText('Ask about this host')).toBeVisible()
    expect(screen.queryByText('old question')).not.toBeInTheDocument()
    await userEvent.type(await composer(), 'fresh{Enter}')
    await vi.waitFor(() => expect(sent('POST /agent/chat')).toHaveLength(1))
    expect(sent('POST /agent/chat')[0]).toEqual({ message: 'fresh', conversation_id: null })
  })

  it('does not stop an answer when Send is double-clicked', async () => {
    const { fetchMock } = agentPage({ chat: { stream: () => {} } })
    const box = await composer()
    await userEvent.type(box, 'hello')
    await userEvent.dblClick(screen.getByRole('button', { name: 'Send' }))
    await screen.findByRole('button', { name: 'Stop' })
    expect(chatCall(fetchMock)![1].signal!.aborted).toBe(false)
  })
})

describe('Agent page: about a finding', () => {
  const FINDING = { body: findingDetail({ id: 'f1', title: 'web keeps restarting' }) }
  const QUESTION = "What's going on here, and what should I check first?"

  it('fills the box with a question, sends nothing, and drops the param', async () => {
    const { sent } = agentPage({ extra: { 'GET /findings/f1': FINDING } }, '/agent?about=f1')
    await vi.waitFor(async () => expect(await composer()).toHaveValue(QUESTION))
    await vi.waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(/^\/agent$/))
    expect(sent('POST /agent/chat')).toHaveLength(0)
  })

  it('shows which finding the conversation is about, with a link to it', async () => {
    agentPage({ extra: { 'GET /findings/f1': FINDING } }, '/agent?about=f1')
    const banner = await screen.findByTestId('agent-finding')
    expect(banner.tagName).toBe('DIV')
    expect(banner).not.toHaveAttribute('role')
    expect(banner).toHaveTextContent('This conversation is about a finding: web keeps restarting')
    expect(within(banner).getByRole('link', { name: 'View finding' })).toHaveAttribute('href', '/findings/f1')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('opens the conversation already started about the finding instead', async () => {
    const stored = {
      id: CID,
      title: 'About: web keeps restarting',
      updated_at: '2026-11-26T15:00:00Z',
      finding_id: 7,
      finding_title: 'web keeps restarting',
      available: true,
      messages: [{ role: 'user', text: 'What is wrong with web?', steps: [] }],
    }
    const { sent } = agentPage(
      {
        conversation: { body: stored },
        extra: { 'GET /findings/7': { body: findingDetail({ id: '7', conversation_id: CID }) } },
      },
      '/agent?about=7',
    )
    expect(await screen.findByRole('article', { name: 'You' })).toHaveTextContent('What is wrong with web?')
    expect(screen.getByTestId('location')).toHaveTextContent(`/agent?c=${CID}`)
    expect(await composer()).toHaveValue('')
    expect(screen.getByTestId('agent-finding')).toHaveTextContent('web keeps restarting')
    expect(sent('POST /agent/chat')).toHaveLength(0)
  })

  it('starts the conversation with the finding, then continues it like any other', async () => {
    const { sent } = agentPage(
      {
        chat: {
          stream: [
            line({ type: 'conversation', id: CID, title: 'About: web keeps restarting', finding_id: 7 }),
            line({ type: 'text', text: 'Look at its memory.' }),
            line({ type: 'done', usage: { input_tokens: 1, output_tokens: 1 } }),
          ],
        },
        extra: { 'GET /findings/7': { body: findingDetail({ id: '7', title: 'web keeps restarting' }) } },
      },
      '/agent?about=7',
    )
    await vi.waitFor(async () => expect(await composer()).toHaveValue(QUESTION))
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    await vi.waitFor(() => expect(sent('POST /agent/chat')).toHaveLength(1))
    expect(sent('POST /agent/chat')[0]).toEqual({ message: QUESTION, conversation_id: null, finding_id: 7 })
    expect(await screen.findByText('Look at its memory.')).toBeVisible()
    await vi.waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(`/agent?c=${CID}`))
    expect(screen.getByTestId('agent-finding')).toHaveTextContent(
      'This conversation is about a finding: web keeps restarting',
    )
    await userEvent.type(await composer(), 'And then?{Enter}')
    await vi.waitFor(() => expect(sent('POST /agent/chat')).toHaveLength(2))
    expect(sent('POST /agent/chat')[1]).toEqual({ message: 'And then?', conversation_id: CID })
  })

  it('says when the finding is gone, and keeps the message', async () => {
    agentPage(
      {
        chat: { status: 404, body: { detail: 'That finding no longer exists.' } },
        extra: { 'GET /findings/7': { body: findingDetail({ id: '7' }) } },
      },
      '/agent?about=7',
    )
    await vi.waitFor(async () => expect(await composer()).toHaveValue(QUESTION))
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('That finding no longer exists.')
    expect(await composer()).toHaveValue(QUESTION)
    expect(screen.queryByRole('article', { name: 'You' })).not.toBeInTheDocument()
  })

  it('shows the finding of a stored conversation', async () => {
    const stored = {
      id: CID,
      title: 't',
      updated_at: '2026-11-26T15:00:00Z',
      finding_id: 7,
      finding_title: '<b>web</b> keeps restarting',
      available: true,
      messages: [],
    }
    agentPage({ conversation: { body: stored } }, `/agent?c=${CID}`)
    const banner = await screen.findByTestId('agent-finding')
    expect(banner).toHaveTextContent('This conversation is about a finding: <b>web</b> keeps restarting')
    expect(within(banner).getByRole('link', { name: 'View finding' })).toHaveAttribute('href', '/findings/7')
  })

  it('still links the finding of a stored conversation when its title is unknown', async () => {
    const stored = {
      id: CID,
      title: 't',
      updated_at: '2026-11-26T15:00:00Z',
      finding_id: 7,
      finding_title: null,
      available: true,
      messages: [],
    }
    agentPage({ conversation: { body: stored } }, `/agent?c=${CID}`)
    const banner = await screen.findByTestId('agent-finding')
    expect(banner).toHaveTextContent(/^This conversation is about a finding\.\s*View finding$/)
    expect(within(banner).getByRole('link', { name: 'View finding' })).toHaveAttribute('href', '/findings/7')
  })

  it('shows no finding for a conversation without one', async () => {
    const plain = {
      id: CID,
      title: 't',
      updated_at: '2026-11-26T15:00:00Z',
      finding_id: null,
      finding_title: null,
      available: true,
      messages: [],
    }
    agentPage({ conversation: { body: plain } }, `/agent?c=${CID}`)
    expect(await composer()).toBeEnabled()
    expect(screen.queryByTestId('agent-finding')).not.toBeInTheDocument()
  })

  it('drops the finding on New conversation', async () => {
    const { sent } = agentPage({ extra: { 'GET /findings/7': { body: findingDetail({ id: '7' }) } } }, '/agent?about=7')
    await screen.findByTestId('agent-finding')
    await userEvent.click(screen.getByRole('button', { name: 'New conversation' }))
    expect(screen.queryByTestId('agent-finding')).not.toBeInTheDocument()
    await userEvent.clear(await composer())
    await userEvent.type(await composer(), 'hello{Enter}')
    await vi.waitFor(() => expect(sent('POST /agent/chat')).toHaveLength(1))
    expect(sent('POST /agent/chat')[0]).toEqual({ message: 'hello', conversation_id: null })
  })

  it('leaves the box empty and shows no error for an unknown id', async () => {
    agentPage(
      { extra: { 'GET /findings/nope': { status: 404, body: { detail: 'No such finding.' } } } },
      '/agent?about=nope',
    )
    expect(await composer()).toHaveValue('')
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(await composer()).toHaveValue('')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('does not overwrite a draft already typed', async () => {
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => (release = resolve))
    agentPage(
      {
        extra: {
          'GET /findings/f1': async () => {
            await gate
            return FINDING
          },
        },
      },
      '/agent?about=f1',
    )
    await userEvent.type(await composer(), 'my own words')
    release()
    await vi.waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent(/^\/agent$/))
    expect(await composer()).toHaveValue('my own words')
  })

  it('ignores about when a conversation is in the address', async () => {
    const stored = { id: CID, title: 't', updated_at: '2026-11-26T15:00:00Z', available: true, messages: [] }
    const { fetchMock } = agentPage({ conversation: { body: stored } }, `/agent?c=${CID}&about=f1`)
    expect(await composer()).toHaveValue('')
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(fetchMock.mock.calls.some(([url]) => String(url).startsWith('/api/findings/f1'))).toBe(false)
    expect(await composer()).toHaveValue('')
  })
})
