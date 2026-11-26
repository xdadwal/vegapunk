/** Shapes the API returns. Times are ISO-8601 UTC strings. */

export type Status = 'online' | 'degraded' | 'offline' | 'unknown'

export type Source = {
  ok: boolean
  stale: boolean
  last_success_at: string | null
  error: string | null
}

export type Capture = {
  interval_seconds: number
  sources: { host: Source; docker: Source }
}

export type Host = {
  /** The server's clock; relative times are measured against it, not the browser's. */
  now: string
  hostname: string | null
  os: string | null
  kernel: string | null
  docker_version: string | null
  cpu_count: number | null
  boot_time: string | null
  latest: {
    observed_at: string
    cpu_pct: number | null
    mem_used: number
    mem_total: number
    disk_used: number
    disk_total: number
  } | null
  containers: { total: number; running: number; needs_attention: number }
  capture: Capture
}

export type HostMetrics = {
  range: string
  resolution_seconds: number
  points: { ts: string; cpu_pct: number | null; mem_pct: number | null; disk_pct: number | null }[]
}

export type ContainerItem = {
  id: string
  name: string
  image: string
  state: string
  health: string | null
  status: Status
  removed: boolean
  restart_count: number
  started_at: string | null
  cpu_pct: number | null
  mem_used: number | null
  mem_limit: number | null
}

export type ContainerMetrics = {
  range: string
  resolution_seconds: number
  points: { ts: string; cpu_pct: number | null; mem_used: number | null; mem_limit: number | null }[]
}

export type ContainerDetail = ContainerItem & { metrics: ContainerMetrics }
