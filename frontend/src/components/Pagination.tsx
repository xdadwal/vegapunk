import { Button } from './Button'

type Props = {
  /** Zero-based. */
  page: number
  pageSize: number
  total: number
  /** Singular noun, such as "finding". */
  noun: string
  onPage: (page: number) => void
}

/** Previous and Next, with how many items match. */
export function Pagination({ page, pageSize, total, noun, onPage }: Props) {
  const last = Math.max(0, Math.ceil(total / pageSize) - 1)
  return (
    <div className="vp-pager">
      <span className="vp-pager-count" aria-live="polite">
        {total} {total === 1 ? `${noun} matches` : `${noun}s match`}
      </span>
      {last > 0 && (
        <div className="vp-pager-buttons">
          <Button size="sm" disabled={page <= 0} onClick={() => onPage(page - 1)}>
            Previous page
          </Button>
          <Button size="sm" disabled={page >= last} onClick={() => onPage(page + 1)}>
            Next page
          </Button>
        </div>
      )}
    </div>
  )
}
