import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router'

import App from './App'
import { findingList } from './test-fixtures'
import { LocationProbe } from './test-probe'

type Reply = {
  status?: number
  body?: unknown
  networkError?: boolean
  /** Answer with a streamed body: lines to send then close, or a function handed the stream's
   * controller so a test can send more later and decide when (or whether) it ends. */
  stream?: string[] | ((controller: ReadableStreamDefaultController<Uint8Array>) => void)
}
type Handler = Reply | ((request: { body: unknown }) => Reply | Promise<Reply>)

export const ADMIN = {
  username: 'admin',
  display_name: 'admin',
  created_at: '2026-01-01T00:00:00Z',
}

/** Stub fetch with replies keyed by "METHOD /path" (no /api prefix). Unmocked calls fail loudly. */
export function stubApi(routes: Record<string, Handler>) {
  // The side navigation asks for the open count on every signed-in page.
  routes = {
    'GET /findings?status=open': { body: findingList() },
    'GET /settings/checks': { body: { enabled: true, hour: 2, watch_enabled: true } },
    ...routes,
  }
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${input.replace(/^\/api/, '')}`
    const handler = routes[key]
    if (!handler) throw new Error(`Unmocked request: ${key}`)
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined
    const reply = await (typeof handler === 'function' ? handler({ body }) : handler)
    if (reply.networkError) throw new TypeError('Failed to fetch')
    const status = reply.status ?? 200
    if (reply.stream) {
      const source = reply.stream
      const encoder = new TextEncoder()
      return new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            if (typeof source === 'function') return source(controller)
            for (const chunk of source) controller.enqueue(encoder.encode(chunk))
            controller.close()
          },
        }),
        { status },
      )
    }
    return new Response(status === 204 ? null : JSON.stringify(reply.body ?? {}), { status })
  })
  vi.stubGlobal('fetch', fetchMock)

  /** Bodies sent to a route, in order. */
  function sent(key: string): unknown[] {
    return fetchMock.mock.calls
      .filter(([url, init]) => `${init?.method ?? 'GET'} ${url.replace(/^\/api/, '')}` === key)
      .map(([, init]) => (typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined))
  }
  return { fetchMock, sent }
}

/** Render the app at `path`. With `probe`, the current URL is shown in a `location` output. */
export function renderApp(path = '/', { probe = false } = {}) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
      {probe && <LocationProbe />}
    </MemoryRouter>,
  )
}
