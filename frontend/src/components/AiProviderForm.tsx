import { useState } from 'react'
import type { FormEvent, ReactNode } from 'react'

import { ApiError } from '../api'
import { useAuth } from '../auth/auth-context'
import type { AiSettings } from '../types'
import { Alert } from './Alert'
import { Button } from './Button'
import { Input } from './Input'
import { Select } from './Select'
import { Textarea } from './Textarea'
import './AiProviderForm.css'

type Props = {
  initial: AiSettings
  onSaved: (saved: AiSettings) => void
  submitLabel?: string
  secondaryAction?: ReactNode
}

type Outcome = { tone: 'success' | 'danger'; text: string }

type TestReply = { ok: true; models: string[] } | { ok: false; code: string; message: string }

function normalise(url: string): string {
  return url.trim().replace(/\/+$/, '')
}

/** The one form for the AI provider, used by Setup step 2 and by Settings. */
export function AiProviderForm({ initial, onSaved, submitLabel = 'Save', secondaryAction }: Props) {
  const { request } = useAuth()
  const [baseUrl, setBaseUrl] = useState(initial.base_url)
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState(initial.model)
  const [instructions, setInstructions] = useState(initial.instructions)
  const [models, setModels] = useState<string[] | null>(null)
  const [busy, setBusy] = useState<'test' | 'save' | null>(null)
  const [test, setTest] = useState<Outcome | null>(null)
  const [saved, setSaved] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  const typedKey = apiKey.trim()
  const urlChanged = normalise(baseUrl) !== initial.base_url

  let keyHint: string
  if (!initial.key_saved) keyHint = 'Leave blank for a server that needs no key.'
  else if (urlChanged)
    keyHint = `The saved key is for ${initial.base_url}. Enter the key for this server, or remove it.`
  else keyHint = `Saved key ends in ${initial.key_hint}. Leave blank to keep it.`

  function edited() {
    setSaved(false)
    setFailure(null)
  }

  async function testConnection() {
    setBusy('test')
    setTest(null)
    edited()
    try {
      const reply = await request<TestReply>('/settings/ai/test', {
        method: 'POST',
        body: { base_url: baseUrl, api_key: typedKey || null },
      })
      if (reply.ok) {
        setModels(reply.models)
        const n = reply.models.length
        setTest({ tone: 'success', text: `Connected. ${n} ${n === 1 ? 'model' : 'models'} available.` })
      } else {
        setTest({ tone: 'danger', text: reply.message })
      }
    } catch (error) {
      setTest({ tone: 'danger', text: error instanceof ApiError ? error.detail : "Couldn't reach the server." })
    } finally {
      setBusy(null)
    }
  }

  async function save(removeKey: boolean) {
    setBusy('save')
    setFailure(null)
    setSaved(false)
    const body: Record<string, unknown> = { base_url: baseUrl, model, instructions }
    if (removeKey) body.remove_key = true
    else if (typedKey) body.api_key = typedKey
    try {
      const result = await request<AiSettings>('/settings/ai', { method: 'PUT', body })
      setApiKey('')
      setSaved(true)
      onSaved(result)
    } catch (error) {
      setFailure(error instanceof ApiError ? error.detail : "Couldn't reach the server.")
    } finally {
      setBusy(null)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    void save(false)
  }

  const choices = models ? [...new Set([model, ...models].filter(Boolean))] : null

  return (
    <form className="ai-form" onSubmit={submit} noValidate>
      {failure && <Alert tone="danger" title={failure} />}
      {saved && <Alert tone="success" title="Saved." />}
      <Input
        label="Base URL"
        mono
        autoComplete="off"
        autoCapitalize="none"
        spellCheck={false}
        value={baseUrl}
        onChange={(e) => {
          setBaseUrl(e.target.value)
          setModels(null) // a model list belongs to the server it came from
          setTest(null)
          edited()
        }}
        hint="Any OpenAI-compatible server, such as OpenAI, Ollama or vLLM."
      />
      <Input
        label="API key"
        type="password"
        mono
        autoComplete="off"
        spellCheck={false}
        value={apiKey}
        onChange={(e) => {
          setApiKey(e.target.value)
          edited()
        }}
        hint={keyHint}
      />
      {choices ? (
        <Select
          label="Model"
          value={model}
          onChange={(e) => {
            setModel(e.target.value)
            edited()
          }}
          options={[
            ...(model ? [] : [{ value: '', label: 'Choose a model' }]),
            ...choices.map((m) => ({ value: m, label: m })),
          ]}
        />
      ) : (
        <Input
          label="Model"
          mono
          autoComplete="off"
          spellCheck={false}
          value={model}
          onChange={(e) => {
            setModel(e.target.value)
            edited()
          }}
        />
      )}
      <Textarea
        label="Instructions"
        value={instructions}
        onChange={(e) => {
          setInstructions(e.target.value)
          edited()
        }}
        hint="What the agent should know about this host: what runs here, what matters most, what to leave alone. These go to your AI provider, so leave out passwords and keys."
      />
      {test && <Alert tone={test.tone} title={test.text} />}
      <div className="ai-actions">
        {secondaryAction}
        <span className="ai-actions-spacer" />
        {initial.key_saved && (
          <Button disabled={busy !== null} onClick={() => void save(true)}>
            Remove key
          </Button>
        )}
        <Button disabled={busy !== null} loading={busy === 'test'} onClick={() => void testConnection()}>
          Test connection
        </Button>
        <Button type="submit" variant="primary" disabled={busy !== null} loading={busy === 'save'}>
          {submitLabel}
        </Button>
      </div>
    </form>
  )
}
