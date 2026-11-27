import { formatBytes, formatPercent } from './format'
import type { ContainerItem } from './types'

/** "CPU 5.0% · Memory 512.0 MiB of 1.0 GiB · 2 restarts", with dashes where there is no sample. */
export function usageText(c: ContainerItem): string {
  const parts = [`CPU ${formatPercent(c.cpu_pct)}`]
  if (c.mem_used === null) parts.push('Memory —')
  else if (c.mem_limit === null) parts.push(`Memory ${formatBytes(c.mem_used)}, no limit`)
  else parts.push(`Memory ${formatBytes(c.mem_used)} of ${formatBytes(c.mem_limit)}`)
  if (c.restart_count > 0) parts.push(`${c.restart_count} ${c.restart_count === 1 ? 'restart' : 'restarts'}`)
  return parts.join(' · ')
}
