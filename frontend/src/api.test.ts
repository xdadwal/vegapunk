import { api, ApiError } from './api'

function respond(status: number, body?: string, init?: ResponseInit) {
  return Promise.resolve(new Response(body, { status, ...init }))
}

describe('api', () => {
  it('returns parsed JSON and sends credentials', async () => {
    const fetchMock = vi.fn(() => respond(200, JSON.stringify({ username: 'admin' })))
    vi.stubGlobal('fetch', fetchMock)

    await expect(api('/me')).resolves.toEqual({ username: 'admin' })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/me',
      expect.objectContaining({ credentials: 'same-origin' }),
    )
  })

  it('sends a JSON body with its method', async () => {
    const fetchMock = vi.fn(() => respond(204))
    vi.stubGlobal('fetch', fetchMock)

    await api('/auth/sign-in', { method: 'POST', body: { username: 'a', password: 'b' } })

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(init.method).toBe('POST')
    expect(init.body).toBe(JSON.stringify({ username: 'a', password: 'b' }))
    expect(new Headers(init.headers).get('content-type')).toBe('application/json')
  })

  it('marks every request that changes something with the app header, and no GET', async () => {
    const fetchMock = vi.fn(() => respond(204))
    vi.stubGlobal('fetch', fetchMock)

    await api('/findings')
    for (const method of ['POST', 'PUT', 'DELETE']) await api('/findings/check', { method })

    const sent = (fetchMock.mock.calls as unknown as [string, RequestInit][]).map(([, init]) => [
      init.method,
      new Headers(init.headers).get('x-requested-with'),
    ])
    expect(sent).toEqual([
      ['GET', null],
      ['POST', 'vegapunk'],
      ['PUT', 'vegapunk'],
      ['DELETE', 'vegapunk'],
    ])
  })

  it('returns undefined for 204', async () => {
    vi.stubGlobal('fetch', vi.fn(() => respond(204)))
    await expect(api('/auth/sign-out', { method: 'POST' })).resolves.toBeUndefined()
  })

  it("throws ApiError with the server's detail", async () => {
    vi.stubGlobal('fetch', vi.fn(() => respond(401, JSON.stringify({ detail: 'Not signed in.' }))))
    const error = await api('/me').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 401, detail: 'Not signed in.' })
  })

  it('keeps the whole body so callers can read extra fields', async () => {
    const body = { detail: 'paused', locked_until: '2026-01-01T15:12:00Z' }
    vi.stubGlobal('fetch', vi.fn(() => respond(429, JSON.stringify(body))))
    const error = (await api('/auth/sign-in', { method: 'POST' }).catch((e: unknown) => e)) as ApiError
    expect(error.body).toEqual(body)
  })

  it('falls back to a generic detail', async () => {
    vi.stubGlobal('fetch', vi.fn(() => respond(500, '<html>oops</html>')))
    await expect(api('/me')).rejects.toMatchObject({ status: 500, detail: 'Request failed (500)' })
  })

  it('does not show validation lists as the detail', async () => {
    const body = { detail: [{ loc: ['body', 'password'], msg: 'too short' }] }
    vi.stubGlobal('fetch', vi.fn(() => respond(422, JSON.stringify(body))))
    await expect(api('/setup/admin', { method: 'POST' })).rejects.toMatchObject({
      status: 422,
      detail: 'Request failed (422)',
    })
  })

  it('reports a network failure as status 0', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))))
    await expect(api('/me')).rejects.toMatchObject({
      status: 0,
      detail: "Couldn't reach the server.",
    })
  })
})
