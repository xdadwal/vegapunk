export class ApiError extends Error {
  status: number
  detail: string
  body: unknown

  constructor(status: number, detail: string, body?: unknown) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.body = body
  }
}

type Options = { method?: string; body?: unknown }

function detailOf(body: unknown, status: number): string {
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    const { detail } = body
    if (typeof detail === 'string') return detail
  }
  return `Request failed (${status})`
}

export async function api<T = void>(path: string, options: Options = {}): Promise<T> {
  const headers = new Headers()
  let body: string | undefined
  if (options.body !== undefined) {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify(options.body)
  }

  let response: Response
  try {
    response = await fetch(`/api${path}`, {
      method: options.method ?? 'GET',
      headers,
      body,
      credentials: 'same-origin',
    })
  } catch {
    throw new ApiError(0, "Couldn't reach the server.")
  }

  if (response.status === 204) return undefined as T
  if (!response.ok) throw await errorFromResponse(response)

  const text = await response.text()
  try {
    return (text ? JSON.parse(text) : undefined) as T
  } catch {
    return undefined as T
  }
}

/** The ApiError for a failed response: the server's `detail` when it sent one. */
export async function errorFromResponse(response: Response): Promise<ApiError> {
  const text = await response.text()
  let parsed: unknown
  try {
    parsed = text ? JSON.parse(text) : undefined
  } catch {
    parsed = undefined
  }
  return new ApiError(response.status, detailOf(parsed, response.status), parsed)
}
