export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <span className="vp-spinner">
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true" focusable="false">
        <circle cx={12} cy={12} r={9} stroke="currentColor" strokeWidth={2.5} opacity={0.25} />
        <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" />
      </svg>
    </span>
  )
}
