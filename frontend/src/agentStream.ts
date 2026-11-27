import { ApiError, errorFromResponse } from './api'
import type { ChatEvent } from './types'

/** `finding_id` starts a conversation about that finding; the server ignores it once
 * `conversation_id` is set. */
type Body = { message: string; conversation_id: string | null; finding_id?: number }

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function isEvent(value: unknown): value is ChatEvent {
  return (
    typeof value === 'object' &&
    value !== null &&
    !Array.isArray(value) &&
    typeof (value as { type?: unknown }).type === 'string'
  )
}

/**
 * Ask the agent and read its answer as it is written: one JSON object per line.
 *
 * A failure before streaming starts (a 409 because the agent is busy, say) throws an
 * ApiError whose `body.code` says which. A line that is not valid JSON is skipped.
 * Aborting resolves quietly, and also cancels the body reader, so it returns even when
 * the underlying `fetch` ignores the signal. It only stops reading: the answer goes on on
 * the server until it ends or is stopped (`POST /agent/conversations/<id>/stop`).
 */
export async function streamChat(body: Body, onEvent: (event: ChatEvent) => void, signal?: AbortSignal): Promise<void> {
  await read(
    '/api/agent/chat',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
    onEvent,
    signal,
  )
}

/**
 * Follow the answer the agent is writing in a conversation: what it has written so far, then
 * the rest as it comes. Nothing running throws an ApiError whose `body.code` is `idle`.
 * Aborting only stops reading; the answer goes on on the server.
 */
export async function followAnswer(id: string, onEvent: (event: ChatEvent) => void, signal?: AbortSignal): Promise<void> {
  await read(`/api/agent/conversations/${encodeURIComponent(id)}/stream`, { method: 'GET' }, onEvent, signal)
}

async function read(
  url: string,
  init: RequestInit,
  onEvent: (event: ChatEvent) => void,
  signal: AbortSignal | undefined,
): Promise<void> {
  let response: Response
  try {
    response = await fetch(url, { ...init, credentials: 'same-origin', signal })
  } catch (error) {
    if (isAbort(error)) return
    throw new ApiError(0, "Couldn't reach the server.")
  }
  if (!response.ok) throw await errorFromResponse(response)
  if (!response.body) return

  const reader = response.body.getReader()
  const stop = () => {
    void reader.cancel().catch(() => {})
  }
  signal?.addEventListener('abort', stop)
  if (signal?.aborted) stop()

  const decoder = new TextDecoder()
  let buffer = ''

  function emit(line: string) {
    const text = line.trim()
    if (!text) return
    try {
      const value: unknown = JSON.parse(text)
      if (isEvent(value)) onEvent(value)
    } catch {
      // not JSON: skip it
    }
  }

  try {
    for (;;) {
      let chunk: ReadableStreamReadResult<Uint8Array>
      try {
        chunk = await reader.read()
      } catch (error) {
        if (signal?.aborted || isAbort(error)) return
        throw new ApiError(0, 'The connection was lost.')
      }
      if (chunk.done) break
      buffer += decoder.decode(chunk.value, { stream: true })
      for (let at = buffer.indexOf('\n'); at >= 0; at = buffer.indexOf('\n')) {
        emit(buffer.slice(0, at))
        buffer = buffer.slice(at + 1)
      }
    }
    emit(buffer + decoder.decode())
  } finally {
    signal?.removeEventListener('abort', stop)
  }
}
