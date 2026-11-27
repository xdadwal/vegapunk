import { act, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { CONFIGURED, SOURCE_NEVER, aiSettings, hostPayload, metricsPayload } from '../test-fixtures'
import { ADMIN, renderApp, stubApi } from '../test-utils'

function settingsPage(payload: object | (() => object), extra: Parameters<typeof stubApi>[0] = {}, path = '/settings') {
  const api = stubApi({
    'GET /setup/status': { body: { admin_created: true } },
    'GET /me': { body: ADMIN },
    'GET /settings/ai': typeof payload === 'function' ? () => ({ body: payload() }) : { body: payload },
    'GET /host': {
      body: hostPayload({ capture: { interval_seconds: 15, sources: { host: SOURCE_NEVER, docker: SOURCE_NEVER } } }),
    },
    'GET /host/metrics?range=1h': { body: metricsPayload() },
    ...extra,
  })
  renderApp(path)
  return api
}

describe('Settings', () => {
  it('says AI is off when nothing is saved', async () => {
    settingsPage(aiSettings())
    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeVisible()
    expect(await screen.findByText('AI is off')).toBeVisible()
    expect(screen.getByText('The agent chat is paused until you connect a provider.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Turn off AI' })).not.toBeInTheDocument()
  })

  it('shows the AI provider form with the saved values', async () => {
    settingsPage(CONFIGURED)
    expect(await screen.findByRole('heading', { level: 2, name: 'AI provider' })).toBeVisible()
    expect(await screen.findByLabelText('Base URL')).toHaveValue('https://api.example.com/v1')
    expect(screen.queryByText('AI is off')).not.toBeInTheDocument()
  })

  it('shows the rejected-key banner with the time', async () => {
    settingsPage({ ...CONFIGURED, key_rejected_at: '2026-01-01T15:12:00Z' })
    const alert = await screen.findByText('Your AI provider rejected the API key')
    expect(alert).toBeVisible()
    expect(
      screen.getByText(
        'The agent chat has been paused since 3:12 PM UTC. Monitoring keeps working. Save a working key to resume.',
      ),
    ).toBeVisible()
  })

  it('clears the banner after saving a working key', async () => {
    let state: object = { ...CONFIGURED, key_rejected_at: '2026-01-01T15:12:00Z' }
    settingsPage(() => state, {
      'PUT /settings/ai': () => {
        state = CONFIGURED
        return { body: CONFIGURED }
      },
    })
    await screen.findByText('Your AI provider rejected the API key')
    await userEvent.type(screen.getByLabelText('API key'), 'sk-new-key-123456')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await vi.waitFor(() => expect(screen.queryByText('Your AI provider rejected the API key')).not.toBeInTheDocument())
  })

  it('warns when the saved key cannot be read', async () => {
    settingsPage({ ...CONFIGURED, key_unreadable: true })
    expect(await screen.findByText("The saved API key can't be read. Enter it again.")).toBeVisible()
  })

  it('turns AI off only after the confirmation step', async () => {
    let state: object = CONFIGURED
    const { sent } = settingsPage(() => state, {
      'DELETE /settings/ai': () => {
        state = aiSettings()
        return { status: 204 }
      },
    })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Turn off AI' }))
    expect(sent('DELETE /settings/ai')).toHaveLength(0)
    await user.click(screen.getByRole('button', { name: 'Confirm: turn off AI' }))
    expect(await screen.findByText('AI is off')).toBeVisible()
    expect(sent('DELETE /settings/ai')).toHaveLength(1)
    expect(await screen.findByLabelText('Base URL')).toHaveValue('https://api.openai.com/v1')
  })

  it('cancels the confirmation after five seconds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      settingsPage(CONFIGURED)
      const turnOff = await screen.findByRole('button', { name: 'Turn off AI' })
      await act(async () => {
        turnOff.click()
      })
      expect(screen.getByRole('button', { name: 'Confirm: turn off AI' })).toBeVisible()
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_100)
      })
      expect(screen.getByRole('button', { name: 'Turn off AI' })).toBeVisible()
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows a retryable error when settings cannot be loaded', async () => {
    let attempts = 0
    settingsPage(aiSettings(), {
      'GET /settings/ai': () => {
        attempts += 1
        return attempts === 1 ? { networkError: true } : { body: CONFIGURED }
      },
    })
    expect(await screen.findByText("Couldn't load settings")).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByLabelText('Base URL')).toBeVisible()
  })

  it('is reachable from the account menu', async () => {
    settingsPage(CONFIGURED, {}, '/')
    await userEvent.click(await screen.findByRole('button', { name: /admin/ }))
    const menu = within(screen.getByRole('list', { name: 'Account' }))
    await userEvent.click(menu.getByRole('link', { name: 'Settings' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeVisible()
  })
})
