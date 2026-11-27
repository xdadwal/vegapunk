export function Avatar({ name }: { name: string }) {
  const letter = name.trim().charAt(0).toUpperCase() || '?'
  return (
    <span className="vp-avatar vp-avatar-md" aria-hidden="true">
      {letter}
    </span>
  )
}
