import { formatAgo, formatBytes, formatClock, formatPercent, formatUptime, formatClockSafe } from './format'

const NOW = new Date('2026-01-10T12:00:00Z')
const ago = (seconds: number) => new Date(NOW.getTime() - seconds * 1000).toISOString()

describe('formatBytes', () => {
  it.each([
    [0, '0 B'],
    [512, '512 B'],
    [1536, '1.5 KiB'],
    [3.9 * 1024 ** 3, '3.9 GiB'],
    [4 * 1024 ** 3, '4.0 GiB'],
    [2 * 1024 ** 4, '2.0 TiB'],
  ])('formats %d as %s', (bytes, text) => {
    expect(formatBytes(bytes)).toBe(text)
  })
})

describe('formatPercent', () => {
  it('rounds to whole numbers from 10 up and one decimal below', () => {
    expect(formatPercent(23)).toBe('23%')
    expect(formatPercent(23.6)).toBe('24%')
    expect(formatPercent(4.26)).toBe('4.3%')
    expect(formatPercent(0)).toBe('0.0%')
  })
  it('shows a dash when there is no value', () => {
    expect(formatPercent(null)).toBe('—')
  })
})

describe('formatUptime', () => {
  it.each([
    [41 * 86400, '41 days'],
    [86400, '1 day'],
    [3 * 3600, '3 hours'],
    [3600, '1 hour'],
    [12 * 60, '12 minutes'],
    [60, '1 minute'],
    [20, 'less than a minute'],
  ])('formats %d seconds as %s', (seconds, text) => {
    expect(formatUptime(ago(seconds), NOW)).toBe(text)
  })
})

describe('formatAgo', () => {
  it.each([
    [4, '4s ago'],
    [59, '59s ago'],
    [120, '2 min ago'],
    [3 * 3600, '3 h ago'],
    [86400, '1 day ago'],
    [2 * 86400, '2 days ago'],
  ])('formats %d seconds as %s', (seconds, text) => {
    expect(formatAgo(ago(seconds), NOW)).toBe(text)
  })
  it('says "just now" for a future or zero difference', () => {
    expect(formatAgo(ago(0), NOW)).toBe('just now')
    expect(formatAgo(ago(-30), NOW)).toBe('just now')
  })
})

describe('formatClock', () => {
  it('is a 12-hour clock in UTC with the zone named', () => {
    expect(formatClock('2026-01-01T15:12:00+00:00')).toBe('3:12 PM UTC')
    expect(formatClock('2026-01-01T00:05:00Z')).toBe('12:05 AM UTC')
  })
})

describe('formatClockSafe', () => {
  it('matches formatClock for valid input and echoes invalid input', () => {
    expect(formatClockSafe('2026-01-01T15:12:00Z')).toBe('3:12 PM UTC')
    expect(formatClockSafe('not a date')).toBe('not a date')
  })
})
