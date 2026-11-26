export type Status = 'online' | 'degraded' | 'offline' | 'unknown'

const WORDS: Record<Status, string> = {
  online: 'Online',
  degraded: 'Degraded',
  offline: 'Offline',
  unknown: 'Unknown',
}

/** A dot and a word, so state is never shown by colour alone. */
export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={`vp-badge vp-status-${status}`}>
      <span className="vp-dot" aria-hidden="true" />
      {WORDS[status]}
    </span>
  )
}
