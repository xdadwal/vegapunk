import { CircleAlert, Container, Cpu, HardDrive, MemoryStick } from 'lucide-react'

import { Link } from 'react-router'

import { Alert } from '../components/Alert'
import { Button } from '../components/Button'
import { EmptyState } from '../components/EmptyState'
import { PageHeader } from '../components/PageHeader'
import { Spinner } from '../components/Spinner'
import { Stat } from '../components/Stat'
import { TimeSeriesChart } from '../components/TimeSeriesChart'
import { formatAgo, formatBytes, formatUptime, percentValue as percent } from '../format'
import type { FindingList, Host, HostMetrics } from '../types'
import { usePolling } from '../usePolling'
import { CaptureAlerts } from './CaptureAlerts'
import './overview.css'

function share(used: number, total: number): number | null {
  return total > 0 ? (used / total) * 100 : null
}

const ATTENTION_ROWS = 3

/** Open findings, newest first. Shows nothing when there are none or the list can't be read. */
function NeedsAttention({ list }: { list: FindingList | undefined }) {
  if (!list || list.open_count <= 0) return null
  const n = list.open_count
  return (
    <section className="vp-card page-section" aria-labelledby="attention-h">
      <h2 id="attention-h">Needs attention</h2>
      <div className="vp-list">
        {list.items.slice(0, ATTENTION_ROWS).map((f) => (
          <div key={f.id} className="vp-service vp-service-linked">
            <div className="vp-service-main">
              <div className="vp-service-name">
                <Link className="vp-card-link" to={`/findings/${encodeURIComponent(f.id)}`}>
                  {f.title}
                </Link>
              </div>
              <div className="vp-service-meta vp-mono">{f.subject}</div>
            </div>
          </div>
        ))}
      </div>
      <Link to="/findings">{n === 1 ? 'See the finding' : `See all ${n} findings`}</Link>
    </section>
  )
}

function attention(count: number): string {
  return `${count} ${count === 1 ? 'needs' : 'need'} attention`
}

export default function Overview() {
  const host = usePolling<Host>('/host')
  const metrics = usePolling<HostMetrics>('/host/metrics?range=1h')
  const findings = usePolling<FindingList>('/findings?status=open')
  const h = host.data

  if (host.loading) {
    return (
      <div className="page-stack">
        <PageHeader title="Overview" />
        <div role="status" className="page-note">
          <Spinner /> Loading…
        </div>
      </div>
    )
  }

  if (!h) {
    return (
      <div className="page-stack">
        <PageHeader title="Overview" />
        <EmptyState
          error
          icon={CircleAlert}
          title="Couldn't load the overview"
          action={<Button onClick={() => void host.reload()}>Retry</Button>}
        >
          {host.error?.detail}
        </EmptyState>
      </div>
    )
  }

  const now = new Date(h.now)
  const { host: hostSource, docker } = h.capture.sources
  const starting = !h.latest && [hostSource, docker].every((s) => !s.ok && !s.error && !s.last_success_at)
  const description = [
    h.hostname,
    h.os,
    h.docker_version && `Docker ${h.docker_version}`,
    h.boot_time && `up ${formatUptime(h.boot_time, now)}`,
  ]
    .filter(Boolean)
    .join(' · ')

  const points = metrics.data?.points
  const trend = (pick: 'cpu_pct' | 'mem_pct' | 'disk_pct') => points?.map((p) => p[pick])
  const latest = h.latest

  return (
    <div className="page-stack">
      <PageHeader title="Overview" description={description || undefined} />
      {host.error && <Alert tone="warning" title="Showing the last data we had. Couldn't reach the server." />}
      <CaptureAlerts capture={h.capture} />
      <NeedsAttention list={findings.data} />

      {starting ? (
        <EmptyState title="Nothing to show yet">Vegapunk is starting its first capture.</EmptyState>
      ) : (
        <>
          {latest && <p className="page-note">Last capture {formatAgo(latest.observed_at, now)}</p>}
          <div className="overview-stats">
            <div className="vp-card vp-card-linked">
              <Stat
                to="/containers"
                label="Containers running"
                icon={Container}
                value={`${h.containers.running} of ${h.containers.total}`}
                hint={h.containers.needs_attention > 0 ? attention(h.containers.needs_attention) : undefined}
              />
            </div>
            <div className="vp-card">
              <Stat
                label="CPU"
                icon={Cpu}
                value={percent(latest?.cpu_pct ?? null)}
                unit="%"
                trend={trend('cpu_pct')}
                trendLabel="CPU, last hour"
              />
            </div>
            <div className="vp-card">
              <Stat
                label="Memory used"
                icon={MemoryStick}
                value={percent(latest ? share(latest.mem_used, latest.mem_total) : null)}
                unit="%"
                hint={latest ? `${formatBytes(latest.mem_used)} of ${formatBytes(latest.mem_total)}` : undefined}
                trend={trend('mem_pct')}
                trendLabel="Memory, last hour"
              />
            </div>
            <div className="vp-card">
              <Stat
                label="Storage used"
                icon={HardDrive}
                value={percent(latest ? share(latest.disk_used, latest.disk_total) : null)}
                unit="%"
                hint={latest ? `${formatBytes(latest.disk_used)} of ${formatBytes(latest.disk_total)}` : undefined}
                trend={trend('disk_pct')}
                trendLabel="Storage, last hour"
              />
            </div>
          </div>

          <section className="page-section" aria-labelledby="chart-h">
            <h2 id="chart-h">CPU and memory, last hour</h2>
            {points ? (
              <div className="vp-card">
                <TimeSeriesChart
                  label="CPU and memory, last hour"
                  unit="%"
                  yMax={100}
                  series={[
                    {
                      name: 'CPU',
                      color: 'var(--chart-1)',
                      points: points.map((p) => ({ ts: p.ts, value: p.cpu_pct })),
                    },
                    {
                      name: 'Memory',
                      color: 'var(--chart-2)',
                      points: points.map((p) => ({ ts: p.ts, value: p.mem_pct })),
                    },
                  ]}
                />
              </div>
            ) : (
              <p className="page-note">{metrics.error ? "Couldn't load the chart." : 'Loading…'}</p>
            )}
          </section>
        </>
      )}
    </div>
  )
}
