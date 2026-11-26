import { createContext, useContext } from 'react'

export type Admin = { username: string; display_name: string; created_at: string }

export type AuthState =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'needs-setup' }
  | { status: 'signed-out' }
  | { status: 'signed-in'; admin: Admin }

export type Auth = {
  state: AuthState
  /** Re-read who is signed in, without showing the loading screen. */
  refresh: () => Promise<void>
  /** Show the loading screen, then re-read. For Retry after a failed load. */
  retry: () => Promise<void>
  signOut: () => Promise<void>
  /** Replace the signed-in admin's details after an edit. */
  setAdmin: (admin: Admin) => void
  /** Like `api`, but a 401 while signed in sends the person back to Sign in. */
  request: <T = void>(path: string, options?: { method?: string; body?: unknown }) => Promise<T>
}

export const AuthContext = createContext<Auth | null>(null)

export function useAuth(): Auth {
  const auth = useContext(AuthContext)
  if (!auth) throw new Error('useAuth must be used inside <AuthProvider>')
  return auth
}
