const clock = new Intl.DateTimeFormat('en-US', {
  hour: 'numeric',
  minute: '2-digit',
  hour12: true,
  timeZone: 'UTC',
  timeZoneName: 'short',
})

/** "3:12 PM UTC": 12-hour clock with the zone named. Every time is UTC until a time zone setting exists. */
export function formatClock(iso: string): string {
  return clock.format(new Date(iso))
}
