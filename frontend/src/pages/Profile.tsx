import { useState } from 'react'
import type { FormEvent } from 'react'

import { ApiError } from '../api'
import { useAuth } from '../auth/auth-context'
import type { Admin } from '../auth/auth-context'
import { Alert } from '../components/Alert'
import { Button } from '../components/Button'
import { Input } from '../components/Input'
import { PageHeader } from '../components/PageHeader'
import './profile.css'

const USERNAME = /^[a-z0-9_-]{1,32}$/
const MIN_PASSWORD = 10

function asApiError(error: unknown): ApiError {
  return error instanceof ApiError ? error : new ApiError(0, "Couldn't reach the server.")
}

function DetailsForm({ admin }: { admin: Admin }) {
  const { request, setAdmin } = useAuth()
  const [displayName, setDisplayName] = useState(admin.display_name)
  const [username, setUsername] = useState(admin.username)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [errors, setErrors] = useState<{ displayName?: string; username?: string }>({})

  async function submit(event: FormEvent) {
    event.preventDefault()
    const name = displayName.trim()
    const found = {
      displayName: name.length >= 1 && name.length <= 64 ? undefined : 'Enter a name of 1 to 64 characters.',
      username: USERNAME.test(username)
        ? undefined
        : 'Use 1 to 32 lowercase letters, numbers, dashes or underscores.',
    }
    setErrors(found)
    setSaved(false)
    setFailure(null)
    if (found.displayName || found.username) return

    setBusy(true)
    try {
      setAdmin(await request<Admin>('/me', { method: 'PATCH', body: { display_name: name, username } }))
      setSaved(true)
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="vp-card profile-section" aria-labelledby="details-h">
      <h2 id="details-h">Details</h2>
      <form className="profile-form" onSubmit={submit} noValidate>
        {failure && <Alert tone="danger" title={failure.detail} />}
        {saved && <Alert tone="success" title="Details saved" />}
        <Input
          label="Display name"
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          hint="Shown in the sidebar."
          error={errors.displayName}
        />
        <Input
          label="Username"
          mono
          autoCapitalize="none"
          spellCheck={false}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          hint="You sign in with this."
          error={errors.username}
        />
        <Button type="submit" variant="primary" loading={busy}>
          Save details
        </Button>
      </form>
    </section>
  )
}

function PasswordForm() {
  const { request } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [changed, setChanged] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [errors, setErrors] = useState<{ current?: string; next?: string; confirm?: string }>({})

  async function submit(event: FormEvent) {
    event.preventDefault()
    const found = {
      next: next.length >= MIN_PASSWORD ? undefined : `Use at least ${MIN_PASSWORD} characters.`,
      confirm: next === confirm ? undefined : "The two passwords don't match.",
      current: undefined as string | undefined,
    }
    setChanged(false)
    setFailure(null)
    setErrors(found)
    if (found.next || found.confirm) return

    setBusy(true)
    try {
      await request('/me/password', { method: 'POST', body: { current_password: current, new_password: next } })
      setCurrent('')
      setNext('')
      setConfirm('')
      setChanged(true)
    } catch (error) {
      const failed = asApiError(error)
      if (failed.status === 400) setErrors({ current: failed.detail })
      else setFailure(failed)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="vp-card profile-section" aria-labelledby="password-h">
      <h2 id="password-h">Password</h2>
      <form className="profile-form" onSubmit={submit} noValidate>
        {failure && <Alert tone="danger" title={failure.detail} />}
        {changed && <Alert tone="success" title="Password changed. Other sessions were signed out." />}
        <Input
          label="Current password"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          error={errors.current}
        />
        <Input
          label="New password"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          hint={`At least ${MIN_PASSWORD} characters.`}
          error={errors.next}
        />
        <Input
          label="Confirm new password"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          error={errors.confirm}
        />
        <Button type="submit" variant="primary" loading={busy}>
          Change password
        </Button>
      </form>
    </section>
  )
}

export default function Profile() {
  const { state } = useAuth()
  if (state.status !== 'signed-in') return null
  return (
    <>
      <PageHeader title="Profile" description="Your admin account on this Vegapunk server" />
      <div className="profile">
        <DetailsForm admin={state.admin} />
        <PasswordForm />
      </div>
    </>
  )
}
