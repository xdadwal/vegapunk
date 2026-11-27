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
    const provider = screen.getByRole('heading', { level: 2, name: 'AI provider' }).closest('section') as HTMLElement
    await userEvent.click(within(provider).getByRole('button', { name: 'Save' }))
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

describe('Settings checks', () => {
  const checks = (body: object = { enabled: true, hour: 2, watch_enabled: true }, extra: Parameters<typeof stubApi>[0] = {}) =>
    settingsPage(CONFIGURED, { 'GET /settings/checks': { body }, ...extra })
  const section = async () => {
    const box = (await screen.findByRole('heading', { level: 2, name: 'Checks' })).closest('section') as HTMLElement
    await within(box).findAllByRole('switch')
    return box
  }

  it('loads the current values, after the AI provider section', async () => {
    checks({ enabled: true, hour: 14, watch_enabled: false })
    const box = await section()
    expect(within(box).getByRole('switch', { name: 'Run a check every day' })).toBeChecked()
    expect(within(box).getByRole('switch', { name: 'Watch for problems in the background' })).not.toBeChecked()
    expect(within(box).getByLabelText('Time of day (UTC)')).toHaveValue('14')
    expect(within(box).getByRole('option', { name: '2:00 AM UTC' })).toBeInTheDocument()
    expect(within(box).getAllByRole('option')).toHaveLength(24)
    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(headings.indexOf('Checks')).toBeGreaterThan(headings.indexOf('AI provider'))
  })

  it('disables the hour while the daily switch is off, whatever the watch switch says', async () => {
    checks({ enabled: false, hour: 2, watch_enabled: true })
    const box = await section()
    const hour = within(box).getByLabelText('Time of day (UTC)')
    expect(hour).toBeDisabled()
    await userEvent.click(within(box).getByRole('switch', { name: 'Watch for problems in the background' }))
    expect(hour).toBeDisabled()
    await userEvent.click(within(box).getByRole('switch', { name: 'Run a check every day' }))
    expect(hour).toBeEnabled()
    await userEvent.click(within(box).getByRole('switch', { name: 'Watch for problems in the background' }))
    expect(hour).toBeEnabled()
  })

  it('lists the daily switch and hour before the watch switch', async () => {
    checks()
    const box = await section()
    const daily = within(box).getByRole('switch', { name: 'Run a check every day' })
    const watch = within(box).getByRole('switch', { name: 'Watch for problems in the background' })
    const hour = within(box).getByLabelText('Time of day (UTC)')
    expect(within(box).getAllByRole('switch')).toHaveLength(2)
    expect(daily.compareDocumentPosition(hour) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(hour.compareDocumentPosition(watch) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('saves enabled, hour and the watch switch together', async () => {
    const api = checks(undefined, {
      'PUT /settings/checks': { body: { enabled: true, hour: 5, watch_enabled: true } },
    })
    const box = await section()
    await userEvent.selectOptions(within(box).getByLabelText('Time of day (UTC)'), '5')
    await userEvent.click(within(box).getByRole('button', { name: 'Save' }))
    expect(await within(box).findByRole('status')).toHaveTextContent('Saved.')
    expect(api.sent('PUT /settings/checks')).toEqual([{ enabled: true, hour: 5, watch_enabled: true }])
  })

  it('sends watch_enabled false after the watch switch is turned off', async () => {
    const api = checks(undefined, {
      'PUT /settings/checks': { body: { enabled: true, hour: 2, watch_enabled: false } },
    })
    const box = await section()
    const watch = within(box).getByRole('switch', { name: 'Watch for problems in the background' })
    expect(watch).toBeChecked()
    await userEvent.click(watch)
    expect(watch).not.toBeChecked()
    expect(within(box).getByRole('switch', { name: 'Run a check every day' })).toBeChecked()
    await userEvent.click(within(box).getByRole('button', { name: 'Save' }))
    expect(await within(box).findByRole('status')).toHaveTextContent('Saved.')
    expect(api.sent('PUT /settings/checks')).toEqual([{ enabled: true, hour: 2, watch_enabled: false }])
  })

  it('shows the server message when saving fails', async () => {
    checks({ enabled: true, hour: 2 }, { 'PUT /settings/checks': { status: 422, body: { detail: 'Hour out of range.' } } })
    const box = await section()
    await userEvent.click(within(box).getByRole('button', { name: 'Save' }))
    expect(await within(box).findByRole('alert')).toHaveTextContent('Hour out of range.')
  })

  it('explains what each switch does', async () => {
    checks()
    const box = await section()
    expect(within(box).queryByText(/no switch of its own/)).toBeNull()
    expect(
      within(box).getByText(
        /The background watch uses simple rules to notice a restart loop, a memory kill, sustained high use or filling storage, and only then asks your AI provider, at most 20 times a day\./,
      ),
    ).toBeVisible()
    expect(within(box).getByText(/A daily check asks your AI provider to review every container and the host\./)).toBeVisible()
  })

  it('retries loading the checks from their own section', async () => {
    let calls = 0
    settingsPage(CONFIGURED, {
      'GET /settings/checks': () => (++calls === 1 ? { status: 500, body: { detail: 'nope' } } : { body: { enabled: true, hour: 2, watch_enabled: true } }),
    })
    const box = (await screen.findByRole('heading', { level: 2, name: 'Checks' })).closest('section') as HTMLElement
    await userEvent.click(await within(box).findByRole('button', { name: 'Retry' }))
    expect(await within(box).findByRole('switch', { name: 'Run a check every day' })).toBeChecked()
    expect(calls).toBe(2)
  })
})
