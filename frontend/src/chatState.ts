import type { ChatEvent, Step, StoredMessage } from './types'

/** One message as the Agent page keeps it. */
export type Msg = {
  role: 'user' | 'assistant'
  text: string
  steps: Step[]
  /** Set while an answer is being written. */
  status?: 'thinking' | 'streaming'
  /** The reason an answer ended early, shown beneath it. */
  error?: string
}

function lastAssistant(messages: Msg[]): number {
  return messages.length > 0 && messages[messages.length - 1].role === 'assistant' ? messages.length - 1 : -1
}

function updateLast(messages: Msg[], change: (m: Msg) => Msg): Msg[] {
  const at = lastAssistant(messages)
  return at < 0 ? messages : messages.map((m, i) => (i === at ? change(m) : m))
}

/** Fold one event of the stream into the conversation. Never changes its input. */
export function applyEvent(messages: Msg[], event: ChatEvent): Msg[] {
  switch (event.type) {
    case 'text':
      return updateLast(messages, (m) => ({ ...m, text: m.text + event.text, status: 'streaming' }))
    case 'step':
      return updateLast(messages, (m) => {
        const step: Step = { id: event.id, label: event.label, status: event.status }
        const known = m.steps.some((s) => s.id === event.id)
        return { ...m, steps: known ? m.steps.map((s) => (s.id === event.id ? step : s)) : [...m.steps, step] }
      })
    case 'done':
      return updateLast(messages, ({ status: _status, ...m }) => m)
    case 'error':
      return updateLast(messages, ({ status: _status, ...m }) => ({ ...m, error: event.message }))
    default:
      return messages
  }
}

/** The stream has ended, however it ended: stop waiting, and say so if nothing was written
 * and nothing else explains why. */
export function finish(messages: Msg[]): Msg[] {
  return updateLast(messages, ({ status, ...m }) =>
    status && m.text === '' && !m.error && m.steps.length === 0 ? { ...m, text: 'Stopped.' } : m,
  )
}

export function fromStored(stored: StoredMessage[]): Msg[] {
  return stored.map((m) => ({ role: m.role, text: m.text, steps: m.steps }))
}
