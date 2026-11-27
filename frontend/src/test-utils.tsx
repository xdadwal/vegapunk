import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router'

import App from './App'

type Reply = { status?: number; body?: unknown; networkError?: boolean }
type Handler = Reply | ((request: { body: unknown }) => Reply | Promise<Reply>)

export const ADMIN = {
  username: 'admin',
  display_name: 'admin',
  created_at: '2026-01-01T00:00:00Z',
}

/** Stub fetch with replies keyed by "METHOD /path" (no /api prefix). Unmocked calls fail loudly. */
export function stubApi(routes: Record<string, Handler>) {
  const fetchMock = vi.fn(async (input: string, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${input.replace(/^\/api/, '')}`
    const handler = routes[key]
    if (!handler) throw new Error(`Unmocked request: ${key}`)
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined
    const reply = await (typeof handler === 'function' ? handler({ body }) : handler)
    if (reply.networkError) throw new TypeError('Failed to fetch')
    const status = reply.status ?? 200
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

export function renderApp(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )
}
