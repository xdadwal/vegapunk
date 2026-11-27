import { CircleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'

import { ApiError } from '../api'
import { useAuth } from '../auth/auth-context'
import { AiProviderForm } from '../components/AiProviderForm'
import { Alert } from '../components/Alert'
import { Button } from '../components/Button'
import { EmptyState } from '../components/EmptyState'
import { PageHeader } from '../components/PageHeader'
import { Spinner } from '../components/Spinner'
import { formatClock } from '../format'
import type { AiSettings } from '../types'
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
    </div>
  )
}
