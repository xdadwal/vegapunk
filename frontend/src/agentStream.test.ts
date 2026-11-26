import { streamChat } from './agentStream'
import { ApiError } from './api'
import type { ChatEvent } from './types'

const encoder = new TextEncoder()

function streamOf(chunks: string[], { close = true } = {}) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      if (close) controller.close()
    },
  })
}

function respond(chunks: string[], init: ResponseInit = {}) {
  return Promise.resolve(new Response(streamOf(chunks), { status: 200, ...init }))
}

function collect() {
  const events: ChatEvent[] = []
  return { events, onEvent: (event: ChatEvent) => events.push(event) }
}

const BODY = { message: 'hi', conversation_id: null }

describe('streamChat', () => {
  it('calls onEvent for each line, in order', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        respond([
          '{"type":"conversation","id":"abc","title":"hi"}\n{"type":"text","text":"Hello"}\n',
          '{"type":"done","usage":{"input_tokens":1,"output_tokens":2}}\n',
        ]),
      ),
    )
    const { events, onEvent } = collect()
    await streamChat(BODY, onEvent)
    expect(events.map((e) => e.type)).toEqual(['conversation', 'text', 'done'])
    expect(events[1]).toEqual({ type: 'text', text: 'Hello' })
  })

  it('handles a line split across chunks and a last line with no newline', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        respond(['{"type":"te', 'xt","text":"a"}\n{"type":"done","usage":{"input_tokens":1,"output_tokens":2}}']),
      ),
    )
    const { events, onEvent } = collect()
    await streamChat(BODY, onEvent)
    expect(events.map((e) => e.type)).toEqual(['text', 'done'])
  })

  it('handles a multi-byte character split across chunks', async () => {
    const bytes = encoder.encode('{"type":"text","text":"café ☕"}\n')
    const cut = bytes.length - 6 // inside the coffee-cup character
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, cut))
        controller.enqueue(bytes.slice(cut))
        controller.close()
      },
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response(body))),
    )
    const { events, onEvent } = collect()
    await streamChat(BODY, onEvent)
    expect(events).toEqual([{ type: 'text', text: 'café ☕' }])
  })

  it('skips blank and invalid lines without throwing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => respond(['\n\nnot json\n{"type":"text","text":"x"}\n[1,2]\n"str"\n'])),
    )
    const { events, onEvent } = collect()
    await expect(streamChat(BODY, onEvent)).resolves.toBeUndefined()
    expect(events).toEqual([{ type: 'text', text: 'x' }])
  })

  it('sends the body as JSON with credentials', async () => {
    const fetchMock = vi.fn(() => respond([]))
    vi.stubGlobal('fetch', fetchMock)
    await streamChat({ message: 'hello', conversation_id: 'c1' }, () => {})
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/agent/chat')
    expect(init.method).toBe('POST')
    expect(init.credentials).toBe('same-origin')
    expect(new Headers(init.headers).get('content-type')).toBe('application/json')
    expect(JSON.parse(init.body as string)).toEqual({ message: 'hello', conversation_id: 'c1' })
  })

  it('throws ApiError with the code for a 409 before streaming', async () => {
    const body = JSON.stringify({ detail: 'The agent is already answering.', code: 'busy' })
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response(body, { status: 409 }))),
    )
    const error = await streamChat(BODY, () => {}).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 409, detail: 'The agent is already answering.' })
    expect((error as ApiError).body).toMatchObject({ code: 'busy' })
  })

  it('throws ApiError for a failure with no JSON body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response('<html>oops</html>', { status: 502 }))),
    )
    await expect(streamChat(BODY, () => {})).rejects.toMatchObject({ status: 502, detail: 'Request failed (502)' })
  })

  it('reports a network failure as status 0', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )
    await expect(streamChat(BODY, () => {})).rejects.toMatchObject({ status: 0, detail: "Couldn't reach the server." })
  })

  it('resolves without error when aborted before the response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new DOMException('Aborted', 'AbortError'))),
    )
    await expect(streamChat(BODY, () => {}, new AbortController().signal)).resolves.toBeUndefined()
  })

  it('passes the abort signal to fetch', async () => {
    const fetchMock = vi.fn(() => respond([]))
    vi.stubGlobal('fetch', fetchMock)
    const controller = new AbortController()
    await streamChat(BODY, () => {}, controller.signal)
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(init.signal).toBe(controller.signal)
  })

  it('returns promptly on abort even when fetch ignores the signal', async () => {
    // a stream that never ends, from a fetch that does not look at the signal
    const body = streamOf(['{"type":"text","text":"first"}\n'], { close: false })
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response(body))),
    )
    const controller = new AbortController()
    const { events, onEvent } = collect()
    const done = streamChat(
      BODY,
      (event) => {
        onEvent(event)
        controller.abort()
      },
      controller.signal,
    )
    await expect(done).resolves.toBeUndefined()
    expect(events).toHaveLength(1)
  })

  it('stops reading when aborted before any data arrives', async () => {
    const body = streamOf([], { close: false })
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(new Response(body))),
    )
    const controller = new AbortController()
    const done = streamChat(BODY, () => {}, controller.signal)
    setTimeout(() => controller.abort(), 10)
    await expect(done).resolves.toBeUndefined()
  })
})
