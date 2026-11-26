import { CircleAlert, FilterX } from 'lucide-react'
import { useState } from 'react'

import { Alert } from '../components/Alert'
import { Button } from '../components/Button'
import { ContainerRow } from '../components/ContainerRow'
import { EmptyState } from '../components/EmptyState'
import { Icon } from '../components/Icon'
import { Input } from '../components/Input'
import { PageHeader } from '../components/PageHeader'
import { Select } from '../components/Select'
import { Spinner } from '../components/Spinner'
import type { ContainerItem, Host, Status } from '../types'
import { usePolling } from '../usePolling'
import { CaptureAlerts } from './CaptureAlerts'
import './overview.css'

const STATUS_OPTIONS = [
  { value: 'any', label: 'Any status' },
  { value: 'online', label: 'Running' },
  { value: 'degraded', label: 'Restarting' },
  { value: 'offline', label: 'Exited' },
  { value: 'unknown', label: 'No status' },
]

export default function Containers() {
  const list = usePolling<{ total: number; items: ContainerItem[] }>('/containers')
  const host = usePolling<Host>('/host')
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<'any' | Status>('any')

  if (list.loading) {
    return (
      <div className="page-stack">
        <PageHeader title="Containers" />
        <div role="status" className="page-note">
          <Spinner /> Loading…
        </div>
      </div>
    )
  }

  if (!list.data) {
    return (
      <div className="page-stack">
        <PageHeader title="Containers" />
        <EmptyState
          error
          icon={CircleAlert}
          title="Couldn't load containers"
          action={<Button onClick={() => void list.reload()}>Retry</Button>}
        >
          {list.error?.detail}
        </EmptyState>
      </div>
    )
  }

  const items = list.data.items
  const running = items.filter((c) => c.status === 'online').length
  const text = query.trim()
  const needle = text.toLowerCase()
  const shown = items.filter(
    (c) =>
      (!needle || c.name.toLowerCase().includes(needle) || c.image.toLowerCase().includes(needle)) &&
      (status === 'any' || c.status === status),
  )
  const reset = () => {
    setQuery('')
    setStatus('any')
  }

  return (
    <div className="page-stack">
      <PageHeader
        title="Containers"
        description={`${items.length} Docker ${items.length === 1 ? 'container' : 'containers'} · ${running} running`}
      />
      {list.error && <Alert tone="warning" title="Showing the last data we had. Couldn't reach the server." />}
      {host.data && <CaptureAlerts capture={host.data.capture} />}

      {items.length === 0 ? (
        <EmptyState title="No containers">Docker reports no containers on this host.</EmptyState>
      ) : (
        <>
          <div className="filters">
            <Input
              label="Search containers"
              placeholder="name or image"
              mono
              autoComplete="off"
              spellCheck={false}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <Select
              label="Status"
              options={STATUS_OPTIONS}
              value={status}
              onChange={(e) => setStatus(e.target.value as 'any' | Status)}
            />
          </div>
          {shown.length === 0 ? (
            <EmptyState
              title={text ? `No containers match “${text}”` : 'No containers match'}
              action={
                <Button onClick={reset}>
                  <Icon icon={FilterX} />
                  Reset
                </Button>
              }
            />
          ) : (
            <div className="vp-list" role="table" aria-label="Containers">
              {shown.map((c) => (
                <ContainerRow key={c.id} container={c} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
