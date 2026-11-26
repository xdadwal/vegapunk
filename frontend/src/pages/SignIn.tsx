import { useState } from 'react'
import type { FormEvent } from 'react'

import { ApiError } from '../api'
import { useAuth } from '../auth/auth-context'
import { Alert } from '../components/Alert'
import { Button } from '../components/Button'
import { Input } from '../components/Input'
import { formatClock } from '../format'
import './auth.css'

const MISMATCH =
  "That username or password doesn't match. After 5 tries sign-in pauses for 15 minutes."

function lockedUntil(error: ApiError): string | null {
  const body = error.body
  if (typeof body === 'object' && body !== null && 'locked_until' in body) {
    const value = (body as { locked_until: unknown }).locked_until
    if (typeof value === 'string') return value
  }
  return null
}

export default function SignIn() {
  const { request, refresh } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setFailure(null)
    try {
      await request('/auth/sign-in', { method: 'POST', body: { username, password } })
      await refresh()
    } catch (error) {
      setFailure(error instanceof ApiError ? error : new ApiError(0, "Couldn't reach the server."))
      setBusy(false)
    }
  }

  const mismatch = failure?.status === 401
  const paused = failure?.status === 429 ? lockedUntil(failure) : null
  const other = failure && !mismatch && !(failure.status === 429 && paused)

  return (
    <div className="vp-page-center">
      <main id="main" className="auth auth-sign-in">
        <div className="auth-head">
          <span className="vp-wordmark">Vegapunk</span>
          <h1 className="auth-title">Sign in</h1>
        </div>
        <form className="vp-card auth-form" onSubmit={submit}>
          {paused && <Alert tone="warning" title={`Sign-in is paused until ${formatClock(paused)}.`} />}
          {other && <Alert tone="danger" title={failure.detail} />}
          <Input
            label="Username"
            mono
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <Input
            label="Password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={mismatch ? MISMATCH : undefined}
          />
          <Button type="submit" variant="primary" loading={busy}>
            Sign in
          </Button>
        </form>
        <p className="auth-note">
          Forgot your password? Run <code className="vp-mono">docker exec -it vegapunk python -m vegapunk reset-password</code>{' '}
          on the host.
        </p>
      </main>
    </div>
  )
}
