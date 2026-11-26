import type { EvidenceLine } from '../types'
import './evidence.css'

const clock = new Intl.DateTimeFormat('en-US', {
  hour: 'numeric',
  minute: '2-digit',
  second: '2-digit',
  hour12: true,
  timeZone: 'UTC',
})

/** "1:48:02 PM" (UTC). Intl can put a narrow no-break space before AM/PM. */
function formatSeconds(iso: string): string {
  return clock.format(new Date(iso)).replace(/[  ]/g, ' ')
}

type Props = { lines: EvidenceLine[]; title?: string }

/** Lines the agent kept as evidence: monospace, one per row, shown as text and never as HTML. */
export function EvidenceLog({ lines, title }: Props) {
  if (lines.length === 0) return <p className="page-note">No evidence lines were kept.</p>
  const timed = lines.some((line) => line.at)
  return (
    <div className="evidence">
      {title && <div className="evidence-title">{title}</div>}
      <div className="evidence-scroll">
        <div className="evidence-list" role="list" aria-label={title ?? 'Evidence'}>
          {lines.map((line, i) => (
            <div
              key={i}
              role="listitem"
              className={line.source === 'metric' ? 'evidence-row evidence-note' : 'evidence-row'}
            >
              {timed && <span className="evidence-time">{line.at ? formatSeconds(line.at) : null}</span>}
              {line.source === 'metric' && <span className="evidence-label">Measured</span>}
              <span className="evidence-text">{line.text}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
