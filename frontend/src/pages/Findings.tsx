import { CircleAlert, FilterX } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'

import { ApiError } from '../api'
import { useAuth } from '../auth/auth-context'
import { Alert } from '../components/Alert'
import { Button } from '../components/Button'
import { EmptyState } from '../components/EmptyState'
import { Icon } from '../components/Icon'
import { Input } from '../components/Input'
import { PageHeader } from '../components/PageHeader'
import { Pagination } from '../components/Pagination'
import { Select } from '../components/Select'
import { Spinner } from '../components/Spinner'
import { StatusBadge } from '../components/StatusBadge'
import { formatClockSafe, formatHour } from '../format'
import type { FindingItem, FindingList, FindingStatus, Status } from '../types'
import { usePolling } from '../usePolling'
import './overview.css'

const PAGE_SIZE = 10

const STATUS_OPTIONS = [
  { value: 'any', label: 'Any status' },
  { value: 'open', label: 'Needs you' },
  { value: 'resolved', label: 'Resolved' },
  { value: 'reviewed', label: 'Reviewed' },
]

const BADGE: Record<FindingStatus, { status: Status; label: string }> = {
  open: { status: 'offline', label: 'Needs you' },
  resolved: { status: 'online', label: 'Resolved' },
  reviewed: { status: 'unknown', label: 'Reviewed' },
}

function FindingRow({ finding }: { finding: FindingItem }) {
  const badge = BADGE[finding.status]
  return (
    <div className="vp-service vp-service-linked" role="row">
      <div className="vp-service-main" role="cell">
        <div className="vp-service-name">
          <Link className="vp-card-link" to={`/findings/${encodeURIComponent(finding.id)}`}>
            {finding.title}
          </Link>
        </div>
        <div className="vp-service-meta">
          <span className="vp-mono">{finding.subject}</span> · <span>Found {formatClockSafe(finding.first_seen_at)}</span>
        </div>
      </div>
      <div className="vp-service-status" role="cell">
        <StatusBadge status={badge.status} label={badge.label} />
      </div>
    </div>
  )
}

export default function Findings() {
  const { request } = useAuth()
  const list = usePolling<FindingList>('/findings')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<'any' | FindingStatus>('any')
  const [page, setPage] = useState(0)
  const [starting, setStarting] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  async function runCheck() {
    setStarting(true)
    setRefusal(null)
    try {
      await request('/findings/check', { method: 'POST' })
    } catch (error) {
      setRefusal(error instanceof ApiError ? error.detail : "Couldn't reach the server.")
    }
    setStarting(false)
    void list.reload()
  }

  if (list.loading) {
    return (
      <div className="page-stack">
        <PageHeader title="Findings" />
        <div role="status" className="page-note">
          <Spinner /> Loading…
        </div>
      </div>
    )
  }

  if (!list.data) {
    return (
      <div className="page-stack">
        <PageHeader title="Findings" />
        <EmptyState
          error
          icon={CircleAlert}
          title="Couldn't load findings"
          action={<Button onClick={() => void list.reload()}>Retry</Button>}
        >
          {list.error?.detail}
        </EmptyState>
      </div>
    )
  }

  const { items, checks } = list.data
  const text = query.trim()
  const needle = text.toLowerCase()
  const shown = items.filter(
    (f) =>
      (!needle || f.title.toLowerCase().includes(needle) || f.subject.toLowerCase().includes(needle)) &&
      (status === 'any' || f.status === status),
  )
  const last = Math.max(0, Math.ceil(shown.length / PAGE_SIZE) - 1)
  const current = Math.min(page, last)
  const visible = shown.slice(current * PAGE_SIZE, (current + 1) * PAGE_SIZE)
  const reset = () => {
    setQuery('')
    setStatus('any')
    setPage(0)
  }
  const settings = <Link to="/settings">Settings</Link>
  const description = checks.last_run_at
    ? `What the agent noticed in container logs, metrics and storage · last scheduled check ${formatClockSafe(checks.last_run_at)}`
    : 'What the agent noticed in container logs, metrics and storage'

  return (
    <div className="page-stack">
      <PageHeader
        title="Findings"
        description={description}
        actions={
          <Button loading={starting} disabled={checks.running} onClick={() => void runCheck()}>
            Run check now
          </Button>
        }
      />
      {refusal && <Alert tone="warning" title={refusal} />}
      {list.error && <Alert tone="warning" title="Showing the last data we had. Couldn't reach the server." />}
      {checks.skipped === 'off' && (
        <Alert tone="info" title="The agent is off">
          Connect an AI provider in {settings} so it can check for problems. Monitoring keeps working without it.
        </Alert>
      )}
      {checks.skipped === 'key_rejected' && (
        <Alert tone="danger" title="Your AI provider rejected the API key">
          Checks are paused. Save a working key in {settings}.
        </Alert>
      )}
      {checks.skipped === 'unsupported' && (
        <Alert tone="warning" title="Your AI provider can't give structured answers, so checks can't run. Try another model.">
          Change the model in {settings}.
        </Alert>
      )}
      {checks.last_error && <Alert tone="danger" title={`The last check failed: ${checks.last_error}`} />}

      {items.length === 0 ? (
        <EmptyState title="No findings yet">
          {checks.enabled
            ? `The agent checks once a day at ${formatHour(checks.hour)} and watches for restarts, memory kills, high use and filling storage.`
            : 'Daily checks are off. Turn them on in Settings.'}
        </EmptyState>
      ) : (
        <>
          <div className="filters">
            <Input
              label="Search findings"
              placeholder="title or container"
              autoComplete="off"
              spellCheck={false}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                setPage(0)
              }}
            />
            <Select
              label="Status"
              options={STATUS_OPTIONS}
              value={status}
              onChange={(e) => {
                setStatus(e.target.value as 'any' | FindingStatus)
                setPage(0)
              }}
            />
          </div>
          {shown.length === 0 ? (
            <EmptyState
              title="No findings match"
              action={
                <Button onClick={reset}>
                  <Icon icon={FilterX} />
                  Reset
                </Button>
              }
            />
          ) : (
            <>
              <div className="vp-list" role="table" aria-label="Findings">
                {visible.map((f) => (
                  <FindingRow key={f.id} finding={f} />
                ))}
              </div>
              <Pagination page={current} pageSize={PAGE_SIZE} total={shown.length} noun="finding" onPage={setPage} />
            </>
          )}
        </>
      )}
    </div>
  )
}
