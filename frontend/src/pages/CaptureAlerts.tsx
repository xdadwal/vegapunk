import { formatClock } from '../format'
import { Alert } from '../components/Alert'
import type { Capture } from '../types'

/** What is wrong with capturing data, if anything. Shown above every data page. */
export function CaptureAlerts({ capture }: { capture: Capture }) {
  const { host, docker } = capture.sources
  // A source that is failing already explains why its data is old, so only a source that
  // reports success but has gone quiet counts as stale.
  const quiet = [host, docker].filter((s) => s.ok && s.stale && s.last_success_at)
  const since = quiet.map((s) => s.last_success_at as string).sort()[0]

  return (
    <>
      {docker.error && (
        <Alert tone="danger" title="Can't reach Docker">
          {docker.error}
        </Alert>
      )}
      {host.error && (
        <Alert tone="danger" title="Can't read host metrics">
          {host.error}
        </Alert>
      )}
      {since && (
        <Alert tone="warning" title="Data is out of date">
          Nothing has been captured since {formatClock(since)}.
        </Alert>
      )}
    </>
  )
}
