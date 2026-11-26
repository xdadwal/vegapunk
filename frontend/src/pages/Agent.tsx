import { CircleAlert, History, MessageSquarePlus, Send, Square } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { streamChat } from './../agentStream'
import { ApiError } from '../api'
import { useAuth } from '../auth/auth-context'
import { Alert } from '../components/Alert'
import { Button } from '../components/Button'
import { ChatMessage } from '../components/ChatMessage'
import { ChatThread } from '../components/ChatThread'
import { EmptyState } from '../components/EmptyState'
import { Icon } from '../components/Icon'
import { PageHeader } from '../components/PageHeader'
import { Spinner } from '../components/Spinner'
import { Textarea } from '../components/Textarea'
import { applyEvent, finish, fromStored } from '../chatState'
import type { Msg } from '../chatState'
import { formatClock } from '../format'
import type { AiSettings, StoredMessage } from '../types'
import { usePolling } from '../usePolling'
import './agent.css'
import './overview.css'

const MAX_MESSAGE = 4_000

type Detail = { id: string; title: string; updated_at: string; available: boolean; messages: StoredMessage[] }
type Loaded =
  | { id: string; kind: 'ok'; unavailable: boolean }
  | { id: string; kind: 'gone' }
  | { id: string; kind: 'error'; detail: string }
type Notice = { tone: 'warning' | 'danger'; text: string }

function codeOf(error: ApiError): string | undefined {
  const body = error.body
  return typeof body === 'object' && body !== null && 'code' in body
    ? String((body as { code: unknown }).code)
    : undefined
}

export default function Agent() {
  const { request } = useAuth()
  const [params, setParams] = useSearchParams()
  const cParam = params.get('c')
  const settings = usePolling<AiSettings>('/settings/ai', { intervalMs: 30_000 })

  const [messages, setMessages] = useState<Msg[]>([])
  const [draft, setDraft] = useState('')
  const [draftError, setDraftError] = useState<string | null>(null)
  const [streaming, setStreaming] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [ownId, setOwnId] = useState<string | null>(null) // a conversation this page started itself
  const [rejectedNow, setRejectedNow] = useState(false)
  const abort = useRef<AbortController | null>(null)
  const sentAt = useRef(0)
  const requestRef = useRef(request)
  useEffect(() => {
    requestRef.current = request
  }, [request])

  // Load a stored conversation named in the URL, unless this page just created it.
  useEffect(() => {
    if (!cParam || cParam === ownId) return
    let ignore = false
    requestRef.current<Detail>(`/agent/conversations/${encodeURIComponent(cParam)}`).then(
      (detail) => {
        if (ignore) return
        setMessages(fromStored(detail.messages))
        setLoaded({ id: cParam, kind: 'ok', unavailable: !detail.available })
      },
      (error: unknown) => {
        if (ignore) return
        if (error instanceof ApiError && error.status === 404) setLoaded({ id: cParam, kind: 'gone' })
        else
          setLoaded({
            id: cParam,
            kind: 'error',
            detail: error instanceof ApiError ? error.detail : "Couldn't reach the server.",
          })
      },
    )
    return () => {
      ignore = true
    }
  }, [cParam, ownId, attempt])

  // The address lost its conversation (the Agent link in the side nav, or New conversation):
  // show an empty thread, so what is on screen is what the next message goes to.
  const [seenParam, setSeenParam] = useState(cParam)
  if (seenParam !== cParam) {
    setSeenParam(cParam)
    if (cParam === null) {
      setMessages([])
      setLoaded(null)
      setOwnId(null)
      setNotice(null)
      setDraftError(null)
    }
  }
  useEffect(() => {
    if (cParam === null) abort.current?.abort()
  }, [cParam])

  // Leaving the page ends any answer being written.
  useEffect(() => () => abort.current?.abort(), [])

  const loadingHistory = cParam !== null && cParam !== ownId && loaded?.id !== cParam
  const here = loaded && loaded.id === cParam ? loaded : null
  const aiOff = settings.data !== undefined && !settings.data.configured
  const rejectedAt = settings.data?.key_rejected_at ?? null
  const rejected = rejectedNow || rejectedAt !== null
  const blocked =
    !settings.data || aiOff || rejected || loadingHistory || here?.kind === 'gone' || here?.kind === 'error'

  function newConversation() {
    abort.current?.abort()
    setMessages([])
    setLoaded(null)
    setOwnId(null)
    setNotice(null)
    setDraftError(null)
    setParams({}, { replace: true })
  }

  async function send(raw: string) {
    const text = raw.trim()
    if (!text || streaming || blocked) return
    if ([...text].length > MAX_MESSAGE) {
      setDraftError('Messages can be up to 4,000 characters.')
      return
    }
    setDraftError(null)
    setNotice(null)
    const before = messages
    const conversationId = ownId ?? cParam
    setMessages([
      ...before,
      { role: 'user', text, steps: [] },
      { role: 'assistant', text: '', steps: [], status: 'thinking' },
    ])
    setDraft('')
    setStreaming(true)
    const controller = new AbortController()
    abort.current = controller
    sentAt.current = Date.now()
    let started = false
    try {
      await streamChat(
        { message: text, conversation_id: conversationId },
        (event) => {
          started = true
          if (event.type === 'conversation') {
            setOwnId(event.id)
            setParams({ c: event.id }, { replace: true })
          } else if (event.type === 'error' && event.code === 'key_rejected') {
            setRejectedNow(true)
            void settings.reload()
          }
          setMessages((current) => applyEvent(current, event))
        },
        controller.signal,
      )
      setMessages((current) => finish(current))
    } catch (error) {
      const failure = error instanceof ApiError ? error : new ApiError(0, "Couldn't reach the server.")
      if (started) {
        // The connection broke part-way: keep what arrived and say so under it.
        setMessages((current) =>
          finish(applyEvent(current, { type: 'error', code: 'connection', message: failure.detail })),
        )
      } else {
        // Refused before any answer: take the message back out of the thread.
        setMessages(before)
        setDraft(text)
        const code = codeOf(failure)
        if (failure.status === 409 && code === 'busy') {
          setNotice({ tone: 'warning', text: 'The agent is already answering. Wait for it to finish.' })
        } else if (failure.status === 409 && code === 'ai_off') {
          void settings.reload()
        } else if (failure.status === 404 && conversationId) {
          setLoaded({ id: conversationId, kind: 'gone' })
        } else {
          setNotice({ tone: 'danger', text: failure.detail })
        }
      }
    } finally {
      setStreaming(false)
      abort.current = null
    }
  }

  const header = (
    <PageHeader
      title="Agent"
      description="Reads this host's containers, logs and metrics and answers questions · times in UTC"
      actions={
        <div className="agent-actions">
          <Link className="vp-btn vp-btn-secondary" to="/agent/history">
            <Icon icon={History} />
            History
          </Link>
          <Button onClick={newConversation}>
            <Icon icon={MessageSquarePlus} />
            New conversation
          </Button>
        </div>
      }
    />
  )

  if (settings.loading) {
    return (
      <div className="page-stack">
        {header}
        <div role="status" className="page-note">
          <Spinner /> Loading…
        </div>
      </div>
    )
  }

  if (aiOff) {
    return (
      <div className="page-stack">
        {header}
        <EmptyState
          icon={CircleAlert}
          title="AI is off"
          action={
            <Link className="vp-btn vp-btn-primary" to="/settings">
              Open Settings
            </Link>
          }
        >
          Connect an AI provider to chat with the agent. Monitoring keeps working without it.
        </EmptyState>
      </div>
    )
  }

  const composer = (
    <div className="vp-composer">
      <Textarea
        label="Message the agent"
        labelHidden
        rows={1}
        submitOnEnter
        value={draft}
        disabled={blocked}
        hint="Enter sends. Shift+Enter adds a line."
        error={draftError ?? undefined}
        onChange={(e) => {
          setDraft(e.target.value)
          setDraftError(null)
        }}
        onSubmit={(text) => void send(text)}
      />
      {streaming ? (
        <Button
          onClick={() => {
            // Send turns into Stop in the same place, so a double-click on Send must not stop it.
            if (Date.now() - sentAt.current > 500) abort.current?.abort()
          }}
        >
          <Icon icon={Square} />
          Stop
        </Button>
      ) : (
        <Button variant="primary" disabled={blocked} onClick={() => void send(draft)}>
          <Icon icon={Send} />
          Send
        </Button>
      )}
    </div>
  )

  return (
    <div className="page-stack">
      {header}
      <p className="page-note">The agent only reads. It can't start, stop or change anything.</p>
      {rejected && (
        <Alert tone="danger" title="Your AI provider rejected the API key">
          Chat is paused{rejectedAt ? ` since ${formatClock(rejectedAt)}` : ''}. Save a working key in{' '}
          <Link to="/settings">Settings</Link>.
        </Alert>
      )}
      {notice && <Alert tone={notice.tone} title={notice.text} />}
      {here?.kind === 'gone' && <Alert tone="warning" title="That conversation no longer exists." />}
      {here?.kind === 'ok' && here.unavailable && (
        <Alert tone="info" title="This conversation is no longer available. Start a new one." />
      )}
      {here?.kind === 'error' && (
        <EmptyState
          error
          icon={CircleAlert}
          title="Couldn't load this conversation"
          action={<Button onClick={() => setAttempt((n) => n + 1)}>Retry</Button>}
        >
          {here.detail}
        </EmptyState>
      )}

      <div className="agent-thread">
        <ChatThread composer={composer}>
          {loadingHistory ? (
            <div role="status" className="page-note">
              <Spinner /> Loading…
            </div>
          ) : messages.length === 0 ? (
            <EmptyState title="Ask about this host">
              For example: “Is anything wrong right now?” or “Why is a container restarting?”
            </EmptyState>
          ) : (
            messages.map((m, i) => (
              <div key={i}>
                <ChatMessage role={m.role} text={m.text} steps={m.steps} status={m.status} />
                {m.error && <Alert tone="danger" title={m.error} />}
              </div>
            ))
          )}
        </ChatThread>
      </div>
    </div>
  )
}
