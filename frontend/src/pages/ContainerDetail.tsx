import { CircleAlert, Cpu, History, MemoryStick, RotateCw, Timer } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router'

import { Alert } from '../components/Alert'
import { Button } from '../components/Button'
import { EmptyState } from '../components/EmptyState'
import { PageHeader } from '../components/PageHeader'
import { RangeTabs } from '../components/RangeTabs'
import { Spinner } from '../components/Spinner'
import { Stat } from '../components/Stat'
import { StatusBadge } from '../components/StatusBadge'
import { TimeSeriesChart } from '../components/TimeSeriesChart'
import { RANGES } from '../ranges'
import type { RangeId } from '../ranges'
import { formatAgo, formatBytes, formatUptime, percentValue } from '../format'
import type { ContainerDetail as Detail } from '../types'
import { usePolling } from '../usePolling'
import './overview.css'

export default function ContainerDetail() {
  const { name = '' } = useParams()
  const [range, setRange] = useState<RangeId>('1h')
  // A day or more of history changes slowly, so it is refreshed less often.
  const { data, error, loading, reload } = usePolling<Detail>(
    `/containers/${encodeURIComponent(name)}?range=${range}`,
    { intervalMs: range === '1h' ? 5000 : 30_000 },
  )
  const phrase = RANGES.find((r) => r.id === range)?.phrase ?? ''
  const crumbs = [{ label: 'Containers', to: '/containers' }, { label: name }]

  if (!data && error?.status === 404) {
    return (
      <div className="page-stack">
        <PageHeader title={name} mono breadcrumbs={crumbs} />
        <EmptyState
          title={`No container named “${name}”`}
          action={
            <Link className="vp-btn vp-btn-secondary" to="/containers">
              Back to containers
            </Link>
          }
        />
      </div>
    )
  }

  if (!data && error) {
    return (
      <div className="page-stack">
        <PageHeader title={name} mono breadcrumbs={crumbs} />
        <EmptyState
          error
          icon={CircleAlert}
          title="Couldn't load this container"
          action={<Button onClick={() => void reload()}>Retry</Button>}
        >
          {error.detail}
        </EmptyState>
      </div>
    )
  }

  const now = data ? new Date(data.now) : null
  const running = !data?.removed && data?.state === 'running'
  const description = data
    ? [
        data.image,
        data.removed ? 'removed' : data.state,
        data.started_at && now && `started ${formatAgo(data.started_at, now)}`,
      ]
        .filter(Boolean)
        .join(' · ')
    : undefined

  return (
    <div className="page-stack">
      <PageHeader
        title={name}
        mono
        breadcrumbs={crumbs}
        description={description}
        meta={data && <StatusBadge status={data.status} />}
      />
      <RangeTabs value={range} onChange={setRange} />

      {loading || !data || !now ? (
        <div role="status" className="page-note">
          <Spinner /> Loading…
        </div>
      ) : (
        <>
          {data.removed && <Alert tone="info" title="This container was removed from the host." />}
          {!data.removed && !running && <Alert tone="info" title="This container isn't running." />}
          {error && <Alert tone="warning" title="Showing the last data we had. Couldn't reach the server." />}

          <div className="overview-stats">
            <div className="vp-card">
              <Stat label="CPU" icon={Cpu} value={percentValue(data.cpu_pct)} unit="%" />
            </div>
            <div className="vp-card">
              <Stat
                label="Memory used"
                icon={MemoryStick}
                value={data.mem_used === null ? null : formatBytes(data.mem_used)}
                hint={
                  data.mem_used === null
                    ? undefined
                    : data.mem_limit === null
                      ? 'no limit'
                      : `of ${formatBytes(data.mem_limit)}`
                }
              />
            </div>
            <div className="vp-card">
              <Stat label="Restarts" icon={RotateCw} value={data.restart_count} />
            </div>
            <div className="vp-card">
              <Stat
                label="Uptime"
                icon={running ? Timer : History}
                value={running && data.started_at ? formatUptime(data.started_at, now) : null}
              />
            </div>
          </div>

          <section className="page-section" aria-labelledby="cpu-h">
            <h2 id="cpu-h">CPU, {phrase}</h2>
            <TimeSeriesChart
              label={`CPU, ${phrase}`}
              unit="%"
              dateAxis={range === '7d' || range === '30d'}
              series={[
                {
                  name: 'CPU',
                  color: 'var(--chart-1)',
                  points: data.metrics.points.map((p) => ({
                    ts: p.ts,
                    value: p.cpu_pct,
                  })),
                },
              ]}
            />
          </section>
          <section className="page-section" aria-labelledby="mem-h">
            <h2 id="mem-h">Memory, {phrase}</h2>
            <TimeSeriesChart
              label={`Memory, ${phrase}`}
              unit="bytes"
              dateAxis={range === '7d' || range === '30d'}
              series={[
                {
                  name: 'Memory used',
                  color: 'var(--chart-2)',
                  points: data.metrics.points.map((p) => ({
                    ts: p.ts,
                    value: p.mem_used,
                  })),
                },
              ]}
            />
          </section>
        </>
      )}
    </div>
  )
}
