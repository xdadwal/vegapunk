/** Intl can put a narrow no-break space before AM/PM depending on the ICU version. */
function plain(text: string): string {
  return text.replace(/[  ]/g, ' ')
}

const clock = new Intl.DateTimeFormat('en-US', {
  hour: 'numeric',
  minute: '2-digit',
  hour12: true,
  timeZone: 'UTC',
  timeZoneName: 'short',
})

const clockOnly = new Intl.DateTimeFormat('en-US', {
  hour: 'numeric',
  minute: '2-digit',
  hour12: true,
  timeZone: 'UTC',
})

const dayOnly = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  timeZone: 'UTC',
})

/** "3:12 PM UTC": 12-hour clock with the zone named. Every time is UTC until a time zone setting exists. */
export function formatClock(iso: string): string {
  return plain(clock.format(new Date(iso)))
}

/** "3:12 PM", for chart axes where the zone is stated once elsewhere. */
export function formatTime(iso: string): string {
  return plain(clockOnly.format(new Date(iso)))
}

/** "Nov 26", for chart axes over days. */
export function formatDay(iso: string): string {
  return plain(dayOnly.format(new Date(iso)))
}

const UNITS = ['KiB', 'MiB', 'GiB', 'TiB']

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${Math.round(bytes)} B`
  let value = bytes
  let unit = -1
  while (value >= 1024 && unit < UNITS.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(1)} ${UNITS[unit]}`
}

/** A percentage without its sign, for a Stat that shows the unit separately: "23", "4.3", or null. */
export function percentValue(value: number | null): string | null {
  if (value === null) return null
  return value < 10 ? value.toFixed(1) : String(Math.round(value))
}

/** "23%", "4.3%" below 10, and a dash for no value. */
export function formatPercent(value: number | null): string {
  if (value === null) return '—'
  return value < 10 ? `${value.toFixed(1)}%` : `${Math.round(value)}%`
}

function plural(count: number, noun: string): string {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

export function formatUptime(bootIso: string, now: Date): string {
  const seconds = Math.max(0, Math.floor((now.getTime() - new Date(bootIso).getTime()) / 1000))
  if (seconds >= 86400) return plural(Math.floor(seconds / 86400), 'day')
  if (seconds >= 3600) return plural(Math.floor(seconds / 3600), 'hour')
  if (seconds >= 60) return plural(Math.floor(seconds / 60), 'minute')
  return 'less than a minute'
}

export function formatAgo(iso: string, now: Date): string {
  const seconds = Math.floor((now.getTime() - new Date(iso).getTime()) / 1000)
  if (seconds <= 0) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`
  return `${plural(Math.floor(seconds / 86400), 'day')} ago`
}
