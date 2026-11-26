export type RangeId = '1h' | '6h' | '24h' | '7d' | '30d'

export const RANGES: { id: RangeId; label: string; phrase: string }[] = [
  { id: '1h', label: '1 h', phrase: 'last hour' },
  { id: '6h', label: '6 h', phrase: 'last 6 hours' },
  { id: '24h', label: '24 h', phrase: 'last 24 hours' },
  { id: '7d', label: '7 d', phrase: 'last 7 days' },
  { id: '30d', label: '30 d', phrase: 'last 30 days' },
]
