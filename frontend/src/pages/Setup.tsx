import { useState } from 'react'
import type { FormEvent } from 'react'

import { ApiError } from '../api'
import { useAuth } from '../auth/auth-context'
import { Alert } from '../components/Alert'
import { Button } from '../components/Button'
import { Input } from '../components/Input'
import './auth.css'

const USERNAME = /^[a-z0-9_-]{1,32}$/
const MIN_PASSWORD = 10

export default function Setup() {
  const { request, refresh } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<{ username?: string; password?: string; confirm?: string }>({})
  const [failure, setFailure] = useState<ApiError | null>(null)

  async function submit(event: FormEvent) {
    event.preventDefault()
    const found = {
      username: USERNAME.test(username) ? undefined : 'Use 1 to 32 lowercase letters, numbers, dashes or underscores.',
      password: [...password].length >= MIN_PASSWORD ? undefined : `Use at least ${MIN_PASSWORD} characters.`,
      confirm: password === confirm ? undefined : "The two passwords don't match.",
    }
    setErrors(found)
    setFailure(null)
    if (found.username || found.password || found.confirm) return

    setBusy(true)
    try {
      await request('/setup/admin', { method: 'POST', body: { username, password } })
      await refresh()
    } catch (error) {
      setFailure(error instanceof ApiError ? error : new ApiError(0, "Couldn't reach the server."))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page-center">
      <main id="main" className="auth auth-setup">
        <div className="auth-head">
          <span className="vp-wordmark">Vegapunk</span>
          <h1 className="auth-title">Set up your control room</h1>
          <p className="auth-lead">
            Create the admin account for this server. You can change these details later in Profile.
          </p>
        </div>
        <form className="vp-card auth-form" onSubmit={submit} noValidate>
          {failure && (
            <Alert
              tone="danger"
              title={failure.detail}
              action={
                failure.status === 409 && (
                  <Button size="sm" onClick={() => void refresh()}>
                    Go to sign in
                  </Button>
                )
              }
            />
          )}
          <Input
            label="Username"
            mono
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            hint="Lowercase letters, numbers, dashes and underscores."
            error={errors.username}
          />
          <Input
            label="Password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            hint={`At least ${MIN_PASSWORD} characters.`}
            error={errors.password}
          />
          <Input
            label="Confirm password"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            error={errors.confirm}
          />
          <Button type="submit" variant="primary" loading={busy}>
            Create account
          </Button>
        </form>
      </main>
    </div>
  )
}
