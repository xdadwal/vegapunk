import { CircleAlert } from 'lucide-react'
import { Link, useNavigate } from 'react-router'

import { AiProviderForm } from '../components/AiProviderForm'
import { Button } from '../components/Button'
import { EmptyState } from '../components/EmptyState'
import { Spinner } from '../components/Spinner'
import type { AiSettings } from '../types'
import { usePolling } from '../usePolling'
import './auth.css'

/** Setup step 2, shown once after the account is created. The same form lives in Settings. */
export default function SetupAi() {
  const navigate = useNavigate()
  const { data, error, loading, reload } = usePolling<AiSettings>('/settings/ai', { intervalMs: 60_000 })

  return (
    <div className="page-center">
      <main id="main" className="auth auth-setup">
        <div className="auth-head">
          <span className="vp-wordmark">Vegapunk</span>
          <h1 className="auth-title">Connect an AI provider</h1>
          <p className="auth-lead">
            The agent chat runs on your own API key. Vegapunk sends the provider only your questions and the logs and
            metrics the agent reads, with secrets redacted.
          </p>
        </div>
        {loading ? (
          <div role="status" className="page-note">
            <Spinner /> Loading…
          </div>
        ) : !data ? (
          <EmptyState
            error
            icon={CircleAlert}
            title="Couldn't load settings"
            action={<Button onClick={() => void reload()}>Retry</Button>}
          >
            {error?.detail}
          </EmptyState>
        ) : (
          <div className="vp-card auth-form">
            <AiProviderForm
              initial={data}
              submitLabel="Save and continue"
              onSaved={() => navigate('/')}
              secondaryAction={
                <Link className="vp-btn vp-btn-ghost" to="/">
                  Skip, use Vegapunk without AI
                </Link>
              }
            />
          </div>
        )}
      </main>
    </div>
  )
}
