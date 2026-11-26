type Props = { values: (number | null)[]; label: string; width?: number; height?: number }

const PAD = 6

export function Sparkline({ values, label, width = 96, height = 32 }: Props) {
  const numbers = values.filter((v): v is number => v !== null)
  if (numbers.length < 2) return null

  const min = Math.min(...numbers)
  const span = Math.max(...numbers) - min || 1
  const x = (i: number) => PAD + (i * (width - 2 * PAD)) / (values.length - 1)
  const y = (v: number) => PAD + (height - 2 * PAD) * (1 - (v - min) / span)

  let path = ''
  let drawing = false
  values.forEach((v, i) => {
    if (v === null) {
      drawing = false
      return
    }
    path += `${drawing ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`
    drawing = true
  })

  let last = values.length - 1
  while (values[last] === null) last -= 1

  return (
    <svg
      className="vp-spark"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={label}
      focusable="false"
    >
      <path d={path} fill="none" className="vp-spark-line" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(last)} cy={y(values[last] as number)} r={4} className="vp-spark-dot" style={{ fill: 'var(--chart-1)' }} />
    </svg>
  )
}
