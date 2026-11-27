import { Bot, Check, X } from 'lucide-react'

import type { Step } from '../types'
import { AgentInline, AgentText } from './AgentText'
import { Icon } from './Icon'
import { Spinner } from './Spinner'

const SPOKEN = { running: 'Running: ', done: 'Done: ', failed: 'Failed: ' }

function Steps({ steps }: { steps: Step[] }) {
  if (steps.length === 0) return null
  return (
    <ul className="vp-steps" aria-label="Steps">
      {steps.map((step, i) => (
        <li key={step.id ? `${step.id}` : i} className={`vp-step vp-step-${step.status}`}>
          <span className="vp-step-icon">
            {step.status === 'running' ? (
              <Spinner size={14} />
            ) : (
              <span style={{ color: step.status === 'failed' ? 'var(--danger)' : 'var(--brand)' }}>
                <Icon icon={step.status === 'failed' ? X : Check} size={14} />
              </span>
            )}
          </span>
          <span className="vp-step-label">
            <span className="vp-sr-only">{SPOKEN[step.status]}</span>
            <AgentInline text={step.label} />
          </span>
        </li>
      ))}
    </ul>
  )
}

type Props = {
  role: 'user' | 'assistant'
  text: string
  steps?: Step[]
  /** The agent's own name; "Agent" by default. */
  name?: string
  status?: 'thinking' | 'streaming'
}

/** One turn: the person's, on the right, or the agent's answer with what it looked at. */
export function ChatMessage({ role, text, steps = [], name = 'Agent', status }: Props) {
  const busy = status === 'streaming' || status === 'thinking'
  const hasText = text.trim() !== ''

  if (role === 'user') {
    return (
      <div className="vp-msg vp-msg-user" role="article" aria-label="You">
        <div className="vp-msg-bubble">
          <div className="vp-msg-body">
            <AgentText text={text} />
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="vp-msg vp-msg-assistant" role="article" aria-label={name} aria-busy={busy || undefined}>
      <div className="vp-msg-meta">
        <Icon icon={Bot} size={14} />
        <span className="vp-msg-name">{name}</span>
      </div>
      <Steps steps={steps} />
      {status === 'thinking' && !hasText && (
        <div className="vp-msg-thinking">
          <Spinner size={14} />
          <span>Thinking…</span>
        </div>
      )}
      {hasText && (
        <div className="vp-msg-body">
          <AgentText text={text} caret={status === 'streaming'} />
        </div>
      )}
    </div>
  )
}
