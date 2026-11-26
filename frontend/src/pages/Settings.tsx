import { CircleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'

import { ApiError } from '../api'
import { useAuth } from '../auth/auth-context'
import { AiProviderForm } from '../components/AiProviderForm'
import { Alert } from '../components/Alert'
import { Button } from '../components/Button'
import { EmptyState } from '../components/EmptyState'
import { PageHeader } from '../components/PageHeader'
import { Select } from '../components/Select'
import { Spinner } from '../components/Spinner'
import { Switch } from '../components/Switch'
import { formatClock, formatHour } from '../format'
import type { AiSettings, CheckSettings } from '../types'
import { usePolling } from '../usePolling'
import './overview.css'
import './profile.css'

const CONFIRM_FOR_MS = 5_000

function TurnOff({ onDone }: { onDone: () => void }) {
  const { request } = useAuth()
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  // The confirmation lapses on its own, so a stray click later does not turn AI off.
  useEffect(() => {
    if (!confirming) return
    const timer = setTimeout(() => setConfirming(false), CONFIRM_FOR_MS)
    return () => clearTimeout(timer)
  }, [confirming])

  async function turnOff() {
    if (!confirming) return setConfirming(true)
    setBusy(true)
    try {
      await request('/settings/ai', { method: 'DELETE' })
      onDone()
    } catch (error) {
      setFailure(error instanceof ApiError ? error.detail : "Couldn't reach the server.")
      setConfirming(false)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="profile-form">
      {failure && <Alert tone="danger" title={failure} />}
      <Button variant={confirming ? 'danger' : 'secondary'} loading={busy} onClick={() => void turnOff()}>
        {confirming ? 'Confirm: turn off AI' : 'Turn off AI'}
      </Button>
    </div>
  )
}

const HOUR_OPTIONS = Array.from({ length: 24 }, (_, hour) => ({ value: String(hour), label: formatHour(hour) }))

function ChecksSection() {
  const { request } = useAuth()
  const [saved, setSaved] = useState<CheckSettings | null>(null)
  const [enabled, setEnabled] = useState(true)
  const [hour, setHour] = useState(0)
  const [loadFailed, setLoadFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [saving, setSaving] = useState(false)
  const [done, setDone] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  useEffect(() => {
    let current = true
    request<CheckSettings>('/settings/checks').then(
      (loaded) => {
        if (!current) return
        setSaved(loaded)
        setEnabled(loaded.enabled)
        setHour(loaded.hour)
        setLoadFailed(false)
      },
      () => current && setLoadFailed(true),
    )
    return () => {
      current = false
    }
  }, [request, attempt])

  async function save() {
    setSaving(true)
    setDone(false)
    setFailure(null)
    try {
      const result = await request<CheckSettings>('/settings/checks', { method: 'PUT', body: { enabled, hour } })
      setSaved(result)
      setDone(true)
    } catch (error) {
      setFailure(error instanceof ApiError ? error.detail : "Couldn't save the check settings.")
    } finally {
      setSaving(false)
    }
  }

  let body
  if (saved) {
    body = (
      <form
        className="profile-form"
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        <p className="page-note">
          A daily check asks your AI provider to review every container and the host. The background watch calls it
          only when something looks wrong (a restart loop, a memory kill, sustained high use or filling storage), at
          most 20 times a day.
        </p>
        <Switch
          label="Run a check every day"
          checked={enabled}
          onChange={(value) => {
            setEnabled(value)
            setDone(false)
          }}
        />
        <Select
          label="Time of day (UTC)"
          options={HOUR_OPTIONS}
          value={String(hour)}
          disabled={!enabled}
          onChange={(e) => {
            setHour(Number(e.target.value))
            setDone(false)
          }}
        />
        {failure && <Alert tone="danger" title={failure} />}
        {done && <Alert tone="success" title="Saved." />}
        <Button type="submit" variant="primary" loading={saving}>
          Save
        </Button>
      </form>
    )
  } else if (loadFailed) {
    body = (
      <div className="profile-form">
        <Alert tone="warning" title="Couldn't load the check settings." />
        <Button onClick={() => setAttempt((n) => n + 1)}>Retry</Button>
      </div>
    )
  } else {
    body = (
      <div role="status" className="page-note">
        <Spinner /> Loading…
      </div>
    )
  }

  return (
    <section className="vp-card profile-section" aria-labelledby="checks-h">
      <h2 id="checks-h">Checks</h2>
      {body}
    </section>
  )
}

export default function Settings() {
  const { data, error, loading, reload } = usePolling<AiSettings>('/settings/ai', { intervalMs: 30_000 })

  if (loading) {
    return (
      <div className="page-stack">
        <PageHeader title="Settings" />
        <div role="status" className="page-note">
          <Spinner /> Loading…
        </div>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="page-stack">
        <PageHeader title="Settings" />
        <EmptyState
          error
          icon={CircleAlert}
          title="Couldn't load settings"
          action={<Button onClick={() => void reload()}>Retry</Button>}
        >
          {error?.detail}
        </EmptyState>
      </div>
    )
  }

  return (
    <div className="page-stack">
      <PageHeader
        title="Settings"
        description="Server settings for this Vegapunk install · changes save when you press Save"
      />
      {!data.configured && (
        <Alert tone="info" title="AI is off">
          The agent chat is paused until you connect a provider.
        </Alert>
      )}
      {data.key_rejected_at && (
        <Alert tone="danger" title="Your AI provider rejected the API key">
          The agent chat has been paused since {formatClock(data.key_rejected_at)}. Monitoring keeps working. Save a
          working key to resume.
        </Alert>
      )}
      {data.key_unreadable && <Alert tone="warning" title="The saved API key can't be read. Enter it again." />}

      <section className="vp-card profile-section" aria-labelledby="ai-h">
        <h2 id="ai-h">AI provider</h2>
        {/* Remount when AI is turned on or off, so the fields start from what is now saved. */}
        <AiProviderForm key={String(data.configured)} initial={data} onSaved={() => void reload()} />
        {data.configured && <TurnOff onDone={() => void reload()} />}
      </section>

      <ChecksSection />
    </div>
  )
}
