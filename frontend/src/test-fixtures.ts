import type { AiSettings, ContainerDetail, ContainerItem, FindingDetail, FindingItem, FindingList, Host, Source } from './types'

export const GIB = 1024 ** 3

export const SOURCE_OK: Source = {
  ok: true,
  stale: false,
  last_success_at: new Date().toISOString(),
  error: null,
}

export const SOURCE_NEVER: Source = {
  ok: false,
  stale: false,
  last_success_at: null,
  error: null,
}

export function hostPayload(over: Partial<Host> = {}): Host {
  const now = Date.now()
  return {
    now: new Date(now).toISOString(),
    hostname: 'nas-01',
    os: 'TrueNAS SCALE',
    kernel: '6.1',
    docker_version: '29.0',
    cpu_count: 8,
    boot_time: new Date(now - 41 * 86400_000).toISOString(),
    latest: {
      observed_at: new Date(now - 4000).toISOString(),
      cpu_pct: 23,
      mem_used: 4 * GIB,
      mem_total: 16 * GIB,
      disk_used: 8 * GIB,
      disk_total: 20 * GIB,
    },
    containers: { total: 12, running: 11, needs_attention: 1 },
    capture: {
      interval_seconds: 15,
      sources: { host: SOURCE_OK, docker: SOURCE_OK },
    },
    ...over,
  }
}

export function metricsPayload(count = 4) {
  const start = Date.now() - count * 15_000
  return {
    range: '1h',
    resolution_seconds: 15,
    points: Array.from({ length: count }, (_, i) => ({
      ts: new Date(start + i * 15_000).toISOString(),
      cpu_pct: 10 + i,
      mem_pct: 25 + i,
      disk_pct: 40,
    })),
  }
}

export function containerItem(over: Partial<ContainerItem> = {}): ContainerItem {
  return {
    id: 'abc',
    name: 'web',
    image: 'nginx:1',
    state: 'running',
    health: null,
    status: 'online',
    removed: false,
    restart_count: 0,
    started_at: new Date(Date.now() - 3 * 3600_000).toISOString(),
    cpu_pct: 5,
    mem_used: 512 * 1024 ** 2,
    mem_limit: GIB,
    ...over,
  }
}

export function detailPayload(over: Partial<ContainerDetail> = {}, range = '1h'): ContainerDetail {
  const start = Date.now() - 4 * 15_000
  return {
    ...containerItem(over),
    now: new Date().toISOString(),
    ...over,
    metrics: {
      range,
      resolution_seconds: 15,
      points: Array.from({ length: 4 }, (_, i) => ({
        ts: new Date(start + i * 15_000).toISOString(),
        cpu_pct: 1 + i,
        mem_used: (100 + i * 10) * 1024 ** 2,
        mem_limit: GIB,
      })),
    },
  }
}

export function aiSettings(over: Partial<AiSettings> = {}): AiSettings {
  return {
    configured: false,
    base_url: 'https://api.openai.com/v1',
    model: '',
    instructions: '',
    key_saved: false,
    key_hint: null,
    key_rejected_at: null,
    key_unreadable: false,
    ...over,
  }
}

/** AI settings as they look once a provider has been saved. */
export const CONFIGURED = aiSettings({
  configured: true,
  base_url: 'https://api.example.com/v1',
  model: 'gpt-x',
  instructions: 'The media stack lives on tank.',
  key_saved: true,
  key_hint: '…1234',
})

export function findingItem(over: Partial<FindingItem> = {}): FindingItem {
  return {
    id: 'f1',
    key: 'restart:web',
    subject: 'web',
    title: 'web keeps restarting',
    status: 'open',
    trigger: 'watch',
    first_seen_at: '2026-11-26T15:00:00Z',
    last_seen_at: '2026-11-26T15:00:00Z',
    reviewed_at: null,
    resolved_at: null,
    ...over,
  }
}

export function findingList(items: FindingItem[] = [], over: Partial<FindingList['checks']> = {}): FindingList {
  return {
    items,
    open_count: items.filter((f) => f.status === 'open').length,
    checks: {
      enabled: true,
      hour: 2,
      last_run_at: null,
      last_error: null,
      skipped: null,
      running: false,
      ...over,
    },
  }
}

export function findingDetail(over: Partial<FindingDetail> = {}): FindingDetail {
  return {
    ...findingItem(),
    diagnosis: 'The web container exits with code 137 every few minutes.',
    suggested_fix: 'Raise the memory limit to 1 GiB.',
    evidence: [
      { source: 'log', text: 'fatal: out of memory', at: '2026-11-26T13:48:02Z' },
      { source: 'metric', text: 'Memory at 98% of its limit', at: null },
    ],
    ...over,
  }
}
