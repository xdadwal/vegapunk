import { ArrowDown } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { Button } from './Button'
import { Icon } from './Icon'

type Props = { children: ReactNode; composer?: ReactNode; label?: string }

const NEAR_BOTTOM = 48

/** The scrolling conversation with its composer beneath. It follows the newest content
 * while the reader is at the bottom, and offers "Jump to latest" when they have scrolled up. */
export function ChatThread({ children, composer, label = 'Agent conversation' }: Props) {
  const logRef = useRef<HTMLDivElement>(null)
  const [showJump, setShowJump] = useState(false)
  // The content height as of the last mutation or scroll: "at the bottom" is judged against
  // the height before a change, so a scroll that has not fired yet still counts.
  const previous = useRef(0)

  useEffect(() => {
    const el = logRef.current
    if (!el || typeof MutationObserver === 'undefined') return
    el.scrollTop = el.scrollHeight
    previous.current = el.scrollHeight
    const observer = new MutationObserver(() => {
      const wasNear = previous.current - el.scrollTop - el.clientHeight < NEAR_BOTTOM
      previous.current = el.scrollHeight
      if (wasNear) {
        el.scrollTop = el.scrollHeight
        previous.current = el.scrollHeight
      } else if (el.scrollHeight > el.clientHeight) {
        setShowJump(true)
      }
    })
    observer.observe(el, { childList: true, subtree: true, characterData: true })
    return () => observer.disconnect()
  }, [])

  function onScroll() {
    const el = logRef.current
    if (!el) return
    previous.current = el.scrollHeight
    if (el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM) setShowJump(false)
  }

  function jump() {
    const el = logRef.current
    if (el) {
      el.scrollTop = el.scrollHeight
      previous.current = el.scrollHeight
    }
    setShowJump(false)
  }

  return (
    <div className="vp-chat">
      <div
        ref={logRef}
        className="vp-chat-log"
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        aria-label={label}
        tabIndex={0}
        onScroll={onScroll}
      >
        {children}
      </div>
      {showJump && (
        <div className="vp-chat-jump">
          <Button size="sm" onClick={jump}>
            <Icon icon={ArrowDown} />
            Jump to latest
          </Button>
        </div>
      )}
      {composer && <div className="vp-chat-composer">{composer}</div>}
    </div>
  )
}
