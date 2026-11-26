import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError } from './api'
import { useAuth } from './auth/auth-context'

type Request = ReturnType<typeof useAuth>['request']

type Result<T> = { data: T } | { error: ApiError }

async function fetchOnce<T>(request: Request, path: string): Promise<Result<T>> {
  try {
    return { data: await request<T>(path) }
  } catch (error) {
    return { error: error instanceof ApiError ? error : new ApiError(0, "Couldn't reach the server.") }
  }
}

type State<T> = { path: string; data?: T; error: ApiError | null; loaded: boolean }

/**
 * Loads `path` and reloads it every `intervalMs`. Keeps the last good data when a later
 * call fails, never has two requests in flight, and pauses while the tab is hidden.
 */
export function usePolling<T>(path: string, options: { intervalMs?: number } = {}) {
  const { intervalMs = 5000 } = options
  const { request } = useAuth()
  const requestRef = useRef(request)
  const runRef = useRef<() => Promise<void>>(async () => {})
  const [state, setState] = useState<State<T>>({ path, error: null, loaded: false })

  useEffect(() => {
    requestRef.current = request
  }, [request])

  useEffect(() => {
    let ignore = false
    let inFlight = false

    function run(): Promise<void> {
      if (inFlight) return Promise.resolve()
      inFlight = true
      return fetchOnce<T>(requestRef.current, path).then((result) => {
        inFlight = false
        if (ignore) return
        setState((previous) => {
          const kept = previous.path === path ? previous.data : undefined
          return 'data' in result
            ? { path, data: result.data, error: null, loaded: true }
            : { path, data: kept, error: result.error, loaded: true }
        })
      })
    }

    runRef.current = run
    void run()
    const timer = setInterval(() => {
      if (!document.hidden) void run()
    }, intervalMs)
    const onVisible = () => {
      if (!document.hidden) void run()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      ignore = true
      clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [path, intervalMs])

  const reload = useCallback(() => runRef.current(), [])

  // State from a previous path is never shown for the current one.
  const current = state.path === path ? state : { path, error: null, loaded: false, data: undefined }
  return { data: current.data, error: current.error, loading: !current.loaded, reload }
}
