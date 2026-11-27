import type { LucideIcon } from 'lucide-react'

type Props = { icon: LucideIcon; size?: number; className?: string }

/** Lucide line icon, 1.75px stroke at 16px and below, 1.5px above (per the design system). */
export function Icon({ icon: Glyph, size = 16, className }: Props) {
  return (
    <Glyph
      size={size}
      strokeWidth={size <= 16 ? 1.75 : 1.5}
      className={className}
      aria-hidden="true"
      focusable="false"
    />
  )
}
