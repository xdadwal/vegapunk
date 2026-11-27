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
  points: {
    ts: string
    cpu_pct: number | null
    mem_pct: number | null
    disk_pct: number | null
  }[]
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
  points: {
    ts: string
    cpu_pct: number | null
    mem_used: number | null
    mem_limit: number | null
  }[]
}

export type ContainerDetail = ContainerItem & {
  now: string
  metrics: ContainerMetrics
}

export type StepStatus = 'running' | 'done' | 'failed'

export type Step = { id?: string; label: string; status: StepStatus }

/** One line of the chat stream. */
export type ChatEvent =
  | { type: 'conversation'; id: string; title: string }
  | { type: 'text'; text: string }
  | { type: 'step'; id: string; label: string; status: StepStatus }
  | { type: 'done'; usage: { input_tokens: number; output_tokens: number } }
  | { type: 'error'; code: string; message: string }

export type Conversation = { id: string; title: string; updated_at: string }

export type StoredMessage = {
  role: 'user' | 'assistant'
  text: string
  steps: { label: string; status: 'done' | 'failed' }[]
}

export type AiSettings = {
  configured: boolean
  base_url: string
  model: string
  instructions: string
  key_saved: boolean
  key_hint: string | null
  key_rejected_at: string | null
  key_unreadable: boolean
}

export type FindingStatus = 'open' | 'resolved' | 'reviewed'

export type FindingItem = {
  id: string
  key: string
  subject: string
  title: string
  status: FindingStatus
  trigger: 'scheduled' | 'watch'
  first_seen_at: string
  last_seen_at: string
  reviewed_at: string | null
  resolved_at: string | null
}

export type FindingChecks = {
  enabled: boolean
  hour: number
  watch_enabled: boolean
  last_run_at: string | null
  last_error: string | null
  skipped: null | 'off' | 'key_rejected' | 'unsupported'
  running: boolean
}

export type FindingList = { items: FindingItem[]; open_count: number; checks: FindingChecks }

export type EvidenceLine = { source: 'log' | 'metric'; text: string; at: string | null }

export type FindingDetail = FindingItem & {
  diagnosis: string
  suggested_fix: string
  evidence: EvidenceLine[]
}

export type CheckSettings = { enabled: boolean; hour: number; watch_enabled: boolean }
