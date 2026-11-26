import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { api, ApiError } from '../api'
import { AuthContext } from './auth-context'
import type { Admin, Auth, AuthState } from './auth-context'

async function loadState(): Promise<AuthState> {
  try {
    const { admin_created } = await api<{ admin_created: boolean }>('/setup/status')
    if (!admin_created) return { status: 'needs-setup' }
    try {
      return { status: 'signed-in', admin: await api<Admin>('/me') }
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return { status: 'signed-out' }
      throw error
    }
  } catch {
    return { status: 'error' }
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: 'loading' })
  const signedIn = useRef(false)

  useEffect(() => {
    signedIn.current = state.status === 'signed-in'
  }, [state])

  const refresh = useCallback(async () => {
    const next = await loadState()
    setState(next)
    return next
  }, [])

  useEffect(() => {
    let ignore = false
    void loadState().then((next) => {
      if (!ignore) setState(next)
    })
    return () => {
      ignore = true
    }
  }, [])

  const value = useMemo<Auth>(
    () => ({
      state,
      refresh,
      retry: async () => {
        setState({ status: 'loading' })
        await refresh()
      },
      signOut: async () => {
        try {
          await api('/auth/sign-out', { method: 'POST' })
        } finally {
          await refresh()
        }
      },
      setAdmin: (admin) => setState({ status: 'signed-in', admin }),
      request: async (path, options) => {
        try {
          return await api(path, options)
        } catch (error) {
          if (error instanceof ApiError && error.status === 401 && signedIn.current) await refresh()
          throw error
        }
      },
    }),
    [state, refresh],
  ) as Auth

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
