import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { CONFIGURED, SOURCE_NEVER, aiSettings, hostPayload, metricsPayload } from '../test-fixtures'
import { ADMIN, renderApp, stubApi } from '../test-utils'

function setupAi(initial = aiSettings()) {
  return stubApi({
    'GET /setup/status': { body: { admin_created: true } },
    'GET /me': { body: ADMIN },
    'GET /settings/ai': { body: initial },
    'PUT /settings/ai': () => ({ body: CONFIGURED }),
    'GET /host': {
      body: hostPayload({ capture: { interval_seconds: 15, sources: { host: SOURCE_NEVER, docker: SOURCE_NEVER } } }),
    },
    'GET /host/metrics?range=1h': { body: metricsPayload() },
  })
}

describe('Setup step 2', () => {
  it('explains what goes to the provider', async () => {
    setupAi()
    renderApp('/setup/ai')
    expect(await screen.findByRole('heading', { level: 1, name: 'Connect an AI provider' })).toBeVisible()
    expect(
      screen.getByText(
        'The agent chat runs on your own API key. Vegapunk sends the provider only your questions and the logs and metrics the agent reads, with secrets redacted.',
      ),
    ).toBeVisible()
  })

  it('saves and continues to the overview', async () => {
    const { sent } = setupAi()
    renderApp('/setup/ai')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Model'), 'gpt-x')
    await user.click(screen.getByRole('button', { name: 'Save and continue' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Overview' })).toBeVisible()
    expect(sent('PUT /settings/ai')).toHaveLength(1)
  })

  it('stays on the page and shows the problem when saving fails', async () => {
    stubApi({
      'GET /setup/status': { body: { admin_created: true } },
      'GET /me': { body: ADMIN },
      'GET /settings/ai': { body: aiSettings() },
      'PUT /settings/ai': { status: 422, body: { detail: 'Enter a model name of 1 to 200 characters.' } },
    })
    renderApp('/setup/ai')
    await userEvent.click(await screen.findByRole('button', { name: 'Save and continue' }))
    expect(await screen.findByText('Enter a model name of 1 to 200 characters.')).toBeVisible()
    expect(screen.getByRole('heading', { level: 1, name: 'Connect an AI provider' })).toBeVisible()
  })

  it('skips without saving anything', async () => {
    const { sent } = setupAi()
    renderApp('/setup/ai')
    await userEvent.click(await screen.findByRole('link', { name: 'Skip, use Vegapunk without AI' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Overview' })).toBeVisible()
    expect(sent('PUT /settings/ai')).toHaveLength(0)
  })

  it('shows a retryable error when settings cannot be loaded', async () => {
    let attempts = 0
    stubApi({
      'GET /setup/status': { body: { admin_created: true } },
      'GET /me': { body: ADMIN },
      'GET /settings/ai': () => {
        attempts += 1
        return attempts === 1 ? { networkError: true } : { body: aiSettings() }
      },
    })
    renderApp('/setup/ai')
    expect(await screen.findByText("Couldn't load settings")).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByLabelText('Base URL')).toBeVisible()
  })
})
