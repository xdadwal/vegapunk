import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'

import { AuthProvider } from '../auth/AuthProvider'
import { CONFIGURED, aiSettings } from '../test-fixtures'
import { ADMIN, stubApi } from '../test-utils'
import type { AiSettings } from '../types'
import { AiProviderForm } from './AiProviderForm'

const BASE = {
  'GET /setup/status': { body: { admin_created: true } },
  'GET /me': { body: ADMIN },
}

function renderForm(initial: AiSettings, routes: Parameters<typeof stubApi>[0] = {}, extra = {}) {
  const api = stubApi({ ...BASE, ...routes })
  const onSaved = vi.fn()
  render(
    <MemoryRouter>
      <AuthProvider>
        <AiProviderForm initial={initial} onSaved={onSaved} {...extra} />
      </AuthProvider>
    </MemoryRouter>,
  )
  return { ...api, onSaved }
}

const field = (name: string) => screen.findByLabelText(name)

describe('AiProviderForm', () => {
  it('shows the saved key as a hint and never as a value', async () => {
    renderForm(CONFIGURED)
    const key = await field('API key')
    expect(key).toHaveValue('')
    expect(key).toHaveAttribute('type', 'password')
    expect(key).toHaveAccessibleDescription('Saved key ends in …1234. Leave blank to keep it.')
    expect(screen.getByRole('button', { name: 'Remove key' })).toBeVisible()
  })

  it('starts with the saved base URL, model and instructions', async () => {
    renderForm(CONFIGURED)
    expect(await field('Base URL')).toHaveValue('https://api.example.com/v1')
    expect(screen.getByLabelText('Model')).toHaveValue('gpt-x')
    expect(screen.getByLabelText('Instructions')).toHaveValue('The media stack lives on tank.')
  })

  it('has a different hint and no Remove key when no key is saved', async () => {
    renderForm(aiSettings())
    expect(await field('API key')).toHaveAccessibleDescription('Leave blank for a server that needs no key.')
    expect(screen.queryByRole('button', { name: 'Remove key' })).not.toBeInTheDocument()
  })

  it('tests the connection and offers the models it found', async () => {
    const { sent } = renderForm(CONFIGURED, {
      'POST /settings/ai/test': { body: { ok: true, models: ['model-a', 'model-b'] } },
    })
    await userEvent.click(await screen.findByRole('button', { name: 'Test connection' }))
    expect(await screen.findByText('Connected. 2 models available.')).toBeVisible()
    expect(sent('POST /settings/ai/test')).toEqual([{ base_url: 'https://api.example.com/v1', api_key: null }])
    const model = screen.getByLabelText('Model')
    expect(model.tagName).toBe('SELECT')
    expect(model).toHaveValue('gpt-x') // the saved model stays offered even if the server does not list it
    expect(screen.getByRole('option', { name: 'model-a' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'gpt-x' })).toBeInTheDocument()
  })

  it('says "1 model" for one', async () => {
    renderForm(CONFIGURED, { 'POST /settings/ai/test': { body: { ok: true, models: ['only-one'] } } })
    await userEvent.click(await screen.findByRole('button', { name: 'Test connection' }))
    expect(await screen.findByText('Connected. 1 model available.')).toBeVisible()
  })

  it('sends the typed key when testing', async () => {
    const { sent } = renderForm(aiSettings(), { 'POST /settings/ai/test': { body: { ok: true, models: [] } } })
    await userEvent.type(await field('API key'), 'sk-typed-key-00001')
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    await screen.findByText(/Connected\./)
    expect(sent('POST /settings/ai/test')).toEqual([
      { base_url: 'https://api.openai.com/v1', api_key: 'sk-typed-key-00001' },
    ])
  })

  it('shows the server message when the key is rejected or the server is unreachable', async () => {
    let reply: object = { ok: false, code: 'key_rejected', message: 'The server rejected the API key.' }
    renderForm(CONFIGURED, { 'POST /settings/ai/test': () => ({ body: reply }) })
    await userEvent.click(await screen.findByRole('button', { name: 'Test connection' }))
    expect(await screen.findByText('The server rejected the API key.')).toBeVisible()
    reply = { ok: false, code: 'unreachable', message: "Couldn't reach https://api.example.com/v1." }
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    expect(await screen.findByText("Couldn't reach https://api.example.com/v1.")).toBeVisible()
    expect(screen.queryByText('The server rejected the API key.')).not.toBeInTheDocument()
  })

  it('shows a network failure while testing', async () => {
    renderForm(CONFIGURED, { 'POST /settings/ai/test': { networkError: true } })
    await userEvent.click(await screen.findByRole('button', { name: 'Test connection' }))
    expect(await screen.findByText("Couldn't reach the server.")).toBeVisible()
  })

  it('saves base URL, model and instructions, and sends the key only when one was typed', async () => {
    const { sent, onSaved } = renderForm(CONFIGURED, { 'PUT /settings/ai': () => ({ body: CONFIGURED }) })
    const model = await field('Model')
    await userEvent.clear(model)
    await userEvent.type(model, 'gpt-y')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Saved.')).toBeVisible()
    expect(sent('PUT /settings/ai')).toEqual([
      { base_url: 'https://api.example.com/v1', model: 'gpt-y', instructions: 'The media stack lives on tank.' },
    ])
    expect(onSaved).toHaveBeenCalledWith(CONFIGURED)

    await userEvent.type(screen.getByLabelText('API key'), 'sk-new-key-123456')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await vi.waitFor(() => expect(sent('PUT /settings/ai')).toHaveLength(2))
    expect(sent('PUT /settings/ai')[1]).toMatchObject({ api_key: 'sk-new-key-123456' })
  })

  it('clears the key field after a successful save', async () => {
    renderForm(aiSettings(), { 'PUT /settings/ai': () => ({ body: CONFIGURED }) })
    await userEvent.type(await field('Model'), 'gpt-x')
    await userEvent.type(screen.getByLabelText('API key'), 'sk-new-key-123456')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await screen.findByText('Saved.')
    expect(screen.getByLabelText('API key')).toHaveValue('')
  })

  it('uses the submit label it is given', async () => {
    renderForm(aiSettings(), {}, { submitLabel: 'Save and continue' })
    expect(await screen.findByRole('button', { name: 'Save and continue' })).toBeVisible()
  })

  it('shows its secondary action', async () => {
    renderForm(aiSettings(), {}, { secondaryAction: <a href="#skip">Skip it</a> })
    expect(await screen.findByRole('link', { name: 'Skip it' })).toBeVisible()
  })

  it('removes the saved key', async () => {
    const { sent, onSaved } = renderForm(CONFIGURED, {
      'PUT /settings/ai': () => ({ body: { ...CONFIGURED, key_saved: false, key_hint: null } }),
    })
    await userEvent.click(await screen.findByRole('button', { name: 'Remove key' }))
    expect(await screen.findByText('Saved.')).toBeVisible()
    expect(sent('PUT /settings/ai')).toEqual([
      {
        base_url: 'https://api.example.com/v1',
        model: 'gpt-x',
        instructions: 'The media stack lives on tank.',
        remove_key: true,
      },
    ])
    expect(onSaved).toHaveBeenCalled()
  })

  it('explains that the saved key belongs to the saved URL when the URL is changed', async () => {
    renderForm(CONFIGURED)
    const url = await field('Base URL')
    await userEvent.clear(url)
    await userEvent.type(url, 'https://other.example.com/v1')
    expect(screen.getByLabelText('API key')).toHaveAccessibleDescription(
      'The saved key is for https://api.example.com/v1. Enter the key for this server, or remove it.',
    )
  })

  it('sends no key when testing a different URL', async () => {
    const { sent } = renderForm(CONFIGURED, { 'POST /settings/ai/test': { body: { ok: true, models: [] } } })
    const url = await field('Base URL')
    await userEvent.clear(url)
    await userEvent.type(url, 'https://other.example.com/v1')
    await userEvent.click(screen.getByRole('button', { name: 'Test connection' }))
    await screen.findByText(/Connected\./)
    expect(sent('POST /settings/ai/test')).toEqual([{ base_url: 'https://other.example.com/v1', api_key: null }])
  })

  it('shows the 422 when saving a changed URL without a new key, and keeps what was typed', async () => {
    renderForm(CONFIGURED, {
      'PUT /settings/ai': {
        status: 422,
        body: { detail: 'You changed the Base URL, so enter the API key again, or remove the saved key.' },
      },
    })
    const url = await field('Base URL')
    await userEvent.clear(url)
    await userEvent.type(url, 'https://other.example.com/v1')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(
      await screen.findByText('You changed the Base URL, so enter the API key again, or remove the saved key.'),
    ).toBeVisible()
    expect(screen.getByLabelText('Base URL')).toHaveValue('https://other.example.com/v1')
    expect(screen.queryByText('Saved.')).not.toBeInTheDocument()
  })

  it('shows a network failure while saving, and keeps what was typed', async () => {
    renderForm(CONFIGURED, { 'PUT /settings/ai': { networkError: true } })
    await userEvent.type(await field('API key'), 'sk-new-key-123456')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText("Couldn't reach the server.")).toBeVisible()
    expect(screen.getByLabelText('API key')).toHaveValue('sk-new-key-123456')
  })

  it('disables the buttons while a request is in flight', async () => {
    renderForm(CONFIGURED, { 'POST /settings/ai/test': () => new Promise<never>(() => {}) })
    await userEvent.click(await screen.findByRole('button', { name: 'Test connection' }))
    expect(screen.getByRole('button', { name: /Test connection/ })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Remove key' })).toBeDisabled()
  })

  it('replaces an old test result when the URL changes', async () => {
    renderForm(CONFIGURED, { 'POST /settings/ai/test': { body: { ok: true, models: ['model-a'] } } })
    await userEvent.click(await screen.findByRole('button', { name: 'Test connection' }))
    await screen.findByText('Connected. 1 model available.')
    await userEvent.type(screen.getByLabelText('Base URL'), 'x')
    expect(screen.queryByText('Connected. 1 model available.')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Model').tagName).toBe('INPUT') // the old list belongs to the old URL
  })
})
