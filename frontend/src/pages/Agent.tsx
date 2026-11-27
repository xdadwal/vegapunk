import { CircleAlert, History, MessageSquarePlus, Send, Square } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router'

import { followAnswer, streamChat } from './../agentStream'
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
import type { AiSettings, ChatEvent, ConversationDetail, FindingDetail } from '../types'
import { usePolling } from '../usePolling'
import './agent.css'
import './overview.css'

const MAX_MESSAGE = 4_000
// The server gives the agent the finding itself, so the question need not repeat it.
const STARTER = "What's going on here, and what should I check first?"
const BUSY = 'The agent is already answering. Wait for it to finish.'
const THINKING: Msg = { role: 'assistant', text: '', steps: [], status: 'thinking' }
const STOPPED: ChatEvent = { type: 'error', code: 'stopped', message: 'Stopped.' }

type Loaded =
  | { id: string; kind: 'ok'; unavailable: boolean }
  | { id: string; kind: 'gone' }
  | { id: string; kind: 'error'; detail: string }
type Notice = { tone: 'warning' | 'danger'; text: string }
/** The finding the conversation on screen is about. `title` is null when the server doesn't know it. */
type About = { id: string; title: string | null }

/** A finding id as the chat API takes it: a positive integer, or nothing. */
function findingNumber(id: string): number | undefined {
  const n = Number(id)
  return /^[1-9][0-9]*$/.test(id) && Number.isSafeInteger(n) ? n : undefined
}

function codeOf(error: ApiError): string | undefined {
  const body = error.body
  return typeof body === 'object' && body !== null && 'code' in body
    ? String((body as { code: unknown }).code)
    : undefined
}

/** Whether two threads would show the same thing, so a reload that changes nothing is not applied. */
function sameThread(a: Msg[], b: Msg[]): boolean {
  const key = (list: Msg[]) =>
    JSON.stringify(
      list.map((m) => [
        m.role,
        m.text,
        m.steps.map((s) => [s.label, s.status]),
        m.status ?? null,
        m.error ?? null,
        m.stopped ?? false,
      ]),
    )
  return key(a) === key(b)
}

export default function Agent() {
  const { request } = useAuth()
  const [params, setParams] = useSearchParams()
  const cParam = params.get('c')
  const aboutParam = params.get('about')
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
  const [about, setAbout] = useState<About | null>(null)
  const abort = useRef<AbortController | null>(null) // the answer being read, sent from here or followed
  const liveId = useRef<string | null>(null) // the conversation of that answer, once known
  const stopping = useRef<AbortController | null>(null) // the read that Stop ended
  const shownId = useRef<string | null>(null) // the conversation on screen
  const sentAt = useRef(0)
  const followRef = useRef<(id: string) => AbortController>(() => new AbortController())
  const requestRef = useRef(request)
  const hasSent = useRef(false) // something was sent from this page
  useEffect(() => {
    requestRef.current = request
  }, [request])
  useEffect(() => {
    shownId.current = ownId ?? cParam
  })

  // Load a stored conversation named in the URL, unless this page just created it. If the agent
  // is still answering in it, follow the answer.
  useEffect(() => {
    if (!cParam || cParam === ownId) return
    let ignore = false
    let following: AbortController | null = null
    requestRef.current<ConversationDetail>(`/agent/conversations/${encodeURIComponent(cParam)}`).then(
      (detail) => {
        if (ignore) return
        const stored = fromStored(detail.messages)
        setMessages(detail.answering ? [...stored, THINKING] : stored)
        setAbout(
          detail.finding_id != null ? { id: String(detail.finding_id), title: detail.finding_title ?? null } : null,
        )
        setLoaded({ id: cParam, kind: 'ok', unavailable: !detail.available })
        if (detail.answering) following = followRef.current(cParam)
      },
      (error: unknown) => {
        if (ignore) return
        setAbout(null)
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
      following?.abort()
    }
  }, [cParam, ownId, attempt])

  // Arriving from a finding (?about=<id>). If it already has a conversation, open that one.
  // Otherwise the next message starts one about it: put a question in the box (never sent, and
  // only into an empty box on a page where nothing has been sent). An unknown id does nothing.
  useEffect(() => {
    if (!aboutParam || cParam) return
    let ignore = false
    requestRef.current<FindingDetail>(`/findings/${encodeURIComponent(aboutParam)}`).then(
      (finding) => {
        if (ignore) return
        if (finding.conversation_id) {
          setParams({ c: finding.conversation_id }, { replace: true })
          return
        }
        setAbout({ id: String(finding.id), title: finding.title })
        if (!hasSent.current) setDraft((current) => (current === '' ? STARTER : current))
        setParams(
          (current) => {
            const next = new URLSearchParams(current)
            next.delete('about')
            return next
          },
          { replace: true },
        )
      },
      () => {
        // Unknown: whatever finding was shown before is not what the next message is about.
        if (!ignore) setAbout(null)
      },
    )
    return () => {
      ignore = true
    }
  }, [aboutParam, cParam, setParams])

  // The address lost its conversation (the Agent link in the side nav, or New conversation):
  // show an empty thread, so what is on screen is what the next message goes to.
  const [seenParam, setSeenParam] = useState(cParam)
  if (seenParam !== cParam) {
    setSeenParam(cParam)
    if (cParam === null) {
      setMessages([])
      setLoaded(null)
      setOwnId(null)
      setAbout(null)
      setNotice(null)
      setDraftError(null)
    }
  }
  useEffect(() => {
    if (cParam === null) abort.current?.abort()
  }, [cParam])

  // Leaving the page stops reading the answer. The answer itself goes on on the server, and
  // coming back follows it again; only Stop ends it.
  useEffect(() => () => abort.current?.abort(), [])

  const loadingHistory = cParam !== null && cParam !== ownId && loaded?.id !== cParam
  const here = loaded && loaded.id === cParam ? loaded : null
  const aiOff = settings.data !== undefined && !settings.data.configured
  const rejectedAt = settings.data?.key_rejected_at ?? null
  const rejected = rejectedNow || rejectedAt !== null
  const blocked =
    !settings.data || aiOff || rejected || loadingHistory || here?.kind === 'gone' || here?.kind === 'error'

  // An answer being written keeps going in its own conversation; this only stops reading it.
  function newConversation() {
    abort.current?.abort()
    setMessages([])
    setLoaded(null)
    setOwnId(null)
    setAbout(null)
    setNotice(null)
    setDraftError(null)
    setParams({}, { replace: true })
  }

  async function loadDetail(id: string): Promise<ConversationDetail | null> {
    try {
      return await requestRef.current<ConversationDetail>(`/agent/conversations/${encodeURIComponent(id)}`)
    } catch {
      return null
    }
  }

  /** Show the conversation as stored, if it is still the one on screen and nothing else is
   * being read into it. */
  function showStored(id: string, detail: ConversationDetail, controller: AbortController) {
    if (shownId.current !== id || (abort.current !== null && abort.current !== controller)) return
    const stored = fromStored(detail.messages)
    setMessages((current) => (sameThread(current, stored) ? current : stored))
  }

  function watch(event: ChatEvent) {
    if (event.type === 'error' && event.code === 'key_rejected') {
      setRejectedNow(true)
      void settings.reload()
    }
    setMessages((current) => applyEvent(current, event))
  }

  /** A read has ended, however it ended. One that Stop ended says Stopped. */
  function settle(controller: AbortController) {
    const stopped = stopping.current === controller
    setMessages((current) => finish(stopped ? applyEvent(current, STOPPED) : current))
  }

  function release(controller: AbortController) {
    if (abort.current !== controller) return
    abort.current = null
    setStreaming(false)
  }

  /** Follow the answer the agent is writing in a conversation whose last message on screen is
   * a waiting answer: what was written so far, then the rest. */
  function follow(id: string): AbortController {
    const controller = new AbortController()
    abort.current = controller
    liveId.current = id
    setStreaming(true)
    void (async () => {
      let done = false
      try {
        await followAnswer(
          id,
          (event) => {
            if (event.type === 'done') done = true
            watch(event)
          },
          controller.signal,
        )
        settle(controller)
      } catch (error) {
        const failure = error instanceof ApiError ? error : new ApiError(0, "Couldn't reach the server.")
        if (failure.status === 404 && codeOf(failure) === 'idle') {
          // It finished between loading the conversation and following it.
          const detail = await loadDetail(id)
          if (detail) showStored(id, detail, controller)
          else
            setMessages((current) =>
              finish(applyEvent(current, { type: 'error', code: 'connection', message: "Couldn't load the answer." })),
            )
        } else {
          setMessages((current) =>
            finish(applyEvent(current, { type: 'error', code: 'connection', message: failure.detail })),
          )
        }
      } finally {
        release(controller)
      }
      // What was followed may have missed steps: show the answer as stored.
      if (done && !controller.signal.aborted) {
        const detail = await loadDetail(id)
        if (detail && !detail.answering) showStored(id, detail, controller)
      }
    })()
    return controller
  }
  useEffect(() => {
    followRef.current = follow
  })

  /** Asking about a finding can carry on the conversation it already has: show what was said
   * in it before this exchange (the thread from `offset` on). */
  async function showEarlier(id: string, offset: number) {
    const detail = await loadDetail(id)
    if (!detail || shownId.current !== id) return
    const stored = fromStored(detail.messages)
    // The server has this exchange too: its question while answering, or question and answer.
    const mine = detail.answering ? 1 : stored.at(-1)?.role === 'assistant' ? 2 : 1
    const earlier = stored.slice(0, Math.max(0, stored.length - mine))
    if (earlier.length > 0) setMessages((current) => [...earlier, ...current.slice(offset)])
  }

  async function stop() {
    // Send turns into Stop in the same place, so a double-click on Send must not stop it.
    if (Date.now() - sentAt.current <= 500) return
    const controller = abort.current
    if (!controller) return
    const id = liveId.current
    stopping.current = controller
    controller.abort()
    if (!id) return
    try {
      await requestRef.current(`/agent/conversations/${encodeURIComponent(id)}/stop`, { method: 'POST' })
    } catch (error) {
      // Idle: it had already finished, so show the answer as stored. Anything else: leave it.
      if (error instanceof ApiError && error.status === 404 && codeOf(error) === 'idle') {
        const detail = await loadDetail(id)
        if (detail && !detail.answering) showStored(id, detail, controller)
      }
    }
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
    hasSent.current = true
    const before = messages
    const conversationId = ownId ?? cParam
    const findingId = !conversationId && about ? findingNumber(about.id) : undefined
    setMessages([...before, { role: 'user', text, steps: [] }, THINKING])
    setDraft('')
    setStreaming(true)
    const controller = new AbortController()
    abort.current = controller
    liveId.current = conversationId
    sentAt.current = Date.now()
    let started = false
    let attach: string | null = null
    try {
      await streamChat(
        findingId === undefined
          ? { message: text, conversation_id: conversationId }
          : { message: text, conversation_id: conversationId, finding_id: findingId },
        (event) => {
          started = true
          if (event.type === 'conversation') {
            liveId.current = event.id
            shownId.current = event.id // before the next render: showEarlier may answer first
            setOwnId(event.id)
            setParams({ c: event.id }, { replace: true })
            const linked = event.finding_id
            setAbout((current) =>
              linked == null
                ? null
                : current?.id === String(linked)
                  ? current
                  : { id: String(linked), title: null },
            )
            if (conversationId === null && linked != null) void showEarlier(event.id, before.length)
          }
          watch(event)
        },
        controller.signal,
      )
      settle(controller)
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
          if (conversationId) attach = conversationId
          else setNotice({ tone: 'warning', text: BUSY })
        } else if (failure.status === 409 && code === 'ai_off') {
          void settings.reload()
        } else if (failure.status === 404 && conversationId) {
          setLoaded({ id: conversationId, kind: 'gone' })
        } else {
          setNotice({ tone: 'danger', text: failure.detail })
        }
      }
    } finally {
      release(controller)
    }

    // Busy in this very conversation (an answer asked for in another tab, say): follow that one.
    if (attach) {
      const detail = await loadDetail(attach)
      if (detail?.answering && shownId.current === attach && abort.current === null) {
        setMessages([...fromStored(detail.messages), THINKING])
        follow(attach)
      } else {
        setNotice({ tone: 'warning', text: BUSY })
      }
    }
  }

  const header = (
    <PageHeader
      title="Agent"
      description="Reads this host's containers, logs and metrics and answers questions · times in UTC"
      actions={
        <div className="agent-actions">
          <Link className="vp-btn vp-btn-quiet" to="/agent/history">
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
        <Button onClick={() => void stop()}>
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

      {about && (
        <div className="agent-finding" data-testid="agent-finding">
          <span className="agent-finding-text">
            {about.title
              ? `This conversation is about a finding: ${about.title}`
              : 'This conversation is about a finding.'}
          </span>{' '}
          <Link to={`/findings/${encodeURIComponent(about.id)}`}>View finding</Link>
        </div>
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
                {m.stopped && <p className="page-note agent-stopped">Stopped.</p>}
              </div>
            ))
          )}
        </ChatThread>
      </div>
    </div>
  )
}
