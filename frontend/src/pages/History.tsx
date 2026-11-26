import { CircleAlert, FilterX, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router'

import { ApiError } from '../api'
import { useAuth } from '../auth/auth-context'
import { Alert } from '../components/Alert'
import { Button } from '../components/Button'
import { EmptyState } from '../components/EmptyState'
import { Icon } from '../components/Icon'
import { Input } from '../components/Input'
import { PageHeader } from '../components/PageHeader'
import { Spinner } from '../components/Spinner'
import { formatDayTime } from '../format'
import type { Conversation } from '../types'
import { usePolling } from '../usePolling'
import './history.css'
import './overview.css'

const CONFIRM_FOR_MS = 5_000

export default function History() {
  const { request } = useAuth()
  const list = usePolling<{ items: Conversation[] }>('/agent/conversations', { intervalMs: 30_000 })
  const [query, setQuery] = useState('')
  const [confirming, setConfirming] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [deleted, setDeleted] = useState<Set<string>>(new Set())
  const [failure, setFailure] = useState<string | null>(null)

  // A pending confirmation lapses on its own, so a stray click later deletes nothing.
  useEffect(() => {
    if (!confirming) return
    const timer = setTimeout(() => setConfirming(null), CONFIRM_FOR_MS)
    return () => clearTimeout(timer)
  }, [confirming])

  async function remove(id: string) {
    setBusy(id)
    setFailure(null)
    try {
      await request(`/agent/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' })
      setDeleted((current) => new Set(current).add(id))
      void list.reload()
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        setDeleted((current) => new Set(current).add(id)) // already gone: that is what was asked
        void list.reload()
      } else {
        setFailure(error instanceof ApiError ? error.detail : "Couldn't reach the server.")
      }
    } finally {
      setBusy(null)
      setConfirming(null)
    }
  }

  const header = (
    <PageHeader
      title="History"
      description="Past conversations with the agent, newest first"
      breadcrumbs={[{ label: 'Agent', to: '/agent' }, { label: 'History' }]}
    />
  )

  if (list.loading) {
    return (
      <div className="page-stack">
        {header}
        <div role="status" className="page-note">
          <Spinner /> Loading…
        </div>
      </div>
    )
  }

  if (!list.data) {
    return (
      <div className="page-stack">
        {header}
        <EmptyState
          error
          icon={CircleAlert}
          title="Couldn't load conversations"
          action={<Button onClick={() => void list.reload()}>Retry</Button>}
        >
          {list.error?.detail}
        </EmptyState>
      </div>
    )
  }

  const all = list.data.items.filter((c) => !deleted.has(c.id))
  const text = query.trim()
  const shown = text ? all.filter((c) => c.title.toLowerCase().includes(text.toLowerCase())) : all

  return (
    <div className="page-stack">
      {header}
      {failure && <Alert tone="danger" title={failure} />}

      {all.length === 0 ? (
        <EmptyState
          title="No conversations yet"
          action={
            <Link className="vp-btn vp-btn-primary" to="/agent">
              Ask the agent
            </Link>
          }
        >
          Conversations with the agent are kept here so you can pick one up again.
        </EmptyState>
      ) : (
        <>
          <Input
            label="Search conversations"
            placeholder="words from the title"
            autoComplete="off"
            spellCheck={false}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {shown.length === 0 ? (
            <EmptyState
              title={`No conversations match “${text}”`}
              action={
                <Button onClick={() => setQuery('')}>
                  <Icon icon={FilterX} />
                  Reset
                </Button>
              }
            />
          ) : (
            <ul className="vp-list" aria-label="Conversations">
              {shown.map((c) => (
                <li key={c.id} className="vp-service vp-service-linked history-row">
                  <div className="vp-service-main">
                    <div className="vp-service-name">
                      <Link className="vp-card-link" to={`/agent?c=${encodeURIComponent(c.id)}`}>
                        {c.title}
                      </Link>
                    </div>
                    <div className="vp-service-meta">{formatDayTime(c.updated_at)} UTC</div>
                  </div>
                  <div className="history-actions">
                    <Button
                      size="sm"
                      variant={confirming === c.id ? 'danger' : 'ghost'}
                      loading={busy === c.id}
                      disabled={busy !== null}
                      onClick={() => (confirming === c.id ? void remove(c.id) : setConfirming(c.id))}
                    >
                      <Icon icon={Trash2} />
                      {confirming === c.id ? 'Confirm delete' : 'Delete'}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}
