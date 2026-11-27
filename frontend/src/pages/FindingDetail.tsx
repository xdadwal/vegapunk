import { CircleAlert } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router'

import { ApiError } from '../api'
import { useAuth } from '../auth/auth-context'
import { Alert } from '../components/Alert'
import { Button } from '../components/Button'
import { EmptyState } from '../components/EmptyState'
import { EvidenceLog } from '../components/EvidenceLog'
import { PageHeader } from '../components/PageHeader'
import { Spinner } from '../components/Spinner'
import { StatusBadge } from '../components/StatusBadge'
import { formatClockSafe } from '../format'
import type { FindingDetail as Detail, FindingStatus, Status } from '../types'
import { usePolling } from '../usePolling'
import './findings.css'
import './overview.css'

const BADGE: Record<FindingStatus, { status: Status; label: string }> = {
  open: { status: 'offline', label: 'Needs you' },
  resolved: { status: 'online', label: 'Resolved' },
  reviewed: { status: 'unknown', label: 'Reviewed' },
}

export default function FindingDetail() {
  const { id = '' } = useParams()
  const { request } = useAuth()
  const { data, error, loading, reload } = usePolling<Detail>(`/findings/${encodeURIComponent(id)}`, {
    intervalMs: 30_000,
  })
  const [reviewed, setReviewed] = useState<string | null>(null)
  const [marking, setMarking] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  async function markReviewed() {
    setMarking(true)
    setFailure(null)
    try {
      await request(`/findings/${encodeURIComponent(id)}/review`, { method: 'POST' })
      setReviewed(id)
    } catch (e) {
      setFailure(e instanceof ApiError ? e.detail : "Couldn't reach the server.")
    }
    setMarking(false)
  }

  if (!data && error?.status === 404) {
    return (
      <div className="page-stack">
        <PageHeader title="Finding" breadcrumbs={[{ label: 'Findings', to: '/findings' }, { label: 'Finding' }]} />
        <EmptyState
          title="This finding no longer exists"
          action={
            <Link className="vp-btn vp-btn-secondary" to="/findings">
              Back to findings
            </Link>
          }
        />
      </div>
    )
  }

  if (!data && error) {
    return (
      <div className="page-stack">
        <PageHeader title="Finding" breadcrumbs={[{ label: 'Findings', to: '/findings' }, { label: 'Finding' }]} />
        <EmptyState
          error
          icon={CircleAlert}
          title="Couldn't load this finding"
          action={<Button onClick={() => void reload()}>Retry</Button>}
        >
          {error.detail}
        </EmptyState>
      </div>
    )
  }

  if (loading || !data) {
    return (
      <div className="page-stack">
        <PageHeader title="Finding" breadcrumbs={[{ label: 'Findings', to: '/findings' }, { label: 'Finding' }]} />
        <div role="status" className="page-note">
          <Spinner /> Loading…
        </div>
      </div>
    )
  }

  const status: FindingStatus = reviewed === data.id && data.status === 'open' ? 'reviewed' : data.status
  const badge = BADGE[status]
  const firstWord = data.title.split(/\s+/)[0]
  const trigger = data.trigger === 'scheduled' ? 'scheduled check' : 'background watch'
  const hasLog = data.evidence.some((line) => line.source === 'log')

  return (
    <div className="page-stack">
      <PageHeader
        title={data.title}
        breadcrumbs={[{ label: 'Findings', to: '/findings' }, { label: `${data.subject} · ${firstWord}` }]}
        meta={<StatusBadge status={badge.status} label={badge.label} />}
        description={`on ${data.subject} · since ${formatClockSafe(data.first_seen_at)} · Found by ${trigger}`}
      />
      {error && <Alert tone="warning" title="Showing the last data we had. Couldn't reach the server." />}
      <article className="finding-article">
        <section className="page-section" aria-labelledby="diagnosis-h">
          <h2 id="diagnosis-h">Diagnosis</h2>
          <p className="finding-text">{data.diagnosis}</p>
        </section>
        <section className="page-section" aria-labelledby="evidence-h">
          <h2 id="evidence-h">Evidence</h2>
          <EvidenceLog lines={data.evidence} title={hasLog ? `docker logs ${data.subject} · times in UTC` : undefined} />
        </section>
        <section className="page-section" aria-labelledby="fix-h">
          <h2 id="fix-h">Suggested fix</h2>
          <p className="finding-text">{data.suggested_fix || "The agent didn't suggest a fix."}</p>
          <p className="page-note">Written by the agent from the logs. Check it before acting on it.</p>
          <p className="page-note">Vegapunk doesn&apos;t change anything on your host.</p>
        </section>
      </article>
      {status === 'resolved' && data.resolved_at && (
        <p className="page-note">This stopped on its own at {formatClockSafe(data.resolved_at)}.</p>
      )}
      {failure && <Alert tone="danger" title={failure} />}
      {data.conversation_id && (
        <p className="page-note">You&apos;ve already asked the agent about this finding.</p>
      )}
      <div className="finding-actions">
        {data.conversation_id ? (
          <Link className="vp-btn vp-btn-secondary" to={`/agent?c=${encodeURIComponent(data.conversation_id)}`}>
            Continue with the agent
          </Link>
        ) : (
          <Link className="vp-btn vp-btn-secondary" to={`/agent?about=${encodeURIComponent(data.id)}`}>
            Ask agent about this finding
          </Link>
        )}
        {status === 'open' && (
          <Button loading={marking} onClick={() => void markReviewed()}>
            Mark as reviewed
          </Button>
        )}
      </div>
    </div>
  )
}
