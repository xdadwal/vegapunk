import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { ChatThread } from './ChatThread'

/** jsdom has no layout, so give the log element scroll geometry we control. */
function geometry(el: HTMLElement, { scrollHeight, clientHeight, scrollTop }: Record<string, number>) {
  const state = { scrollHeight, clientHeight, scrollTop }
  Object.defineProperty(el, 'scrollHeight', { get: () => state.scrollHeight, configurable: true })
  Object.defineProperty(el, 'clientHeight', { get: () => state.clientHeight, configurable: true })
  Object.defineProperty(el, 'scrollTop', {
    get: () => state.scrollTop,
    set: (v: number) => {
      state.scrollTop = v
    },
    configurable: true,
  })
  return state
}

describe('ChatThread', () => {
  it('is a log region with a name and shows its composer', () => {
    render(<ChatThread composer={<input aria-label="Message" />}>content</ChatThread>)
    expect(screen.getByRole('log', { name: 'Agent conversation' })).toHaveTextContent('content')
    expect(screen.getByLabelText('Message')).toBeInTheDocument()
  })

  it('accepts another name', () => {
    render(<ChatThread label="Conversation">x</ChatThread>)
    expect(screen.getByRole('log', { name: 'Conversation' })).toBeInTheDocument()
  })

  it('scrolls to the newest content when already at the bottom', async () => {
    const { rerender } = render(<ChatThread>one</ChatThread>)
    const log = screen.getByRole('log')
    const state = geometry(log, { scrollHeight: 1000, clientHeight: 200, scrollTop: 800 })
    // the observer is created on mount; trigger it with new content
    state.scrollHeight = 1200
    await act(async () => {
      rerender(
        <ChatThread>
          one<p>two</p>
        </ChatThread>,
      )
    })
    await vi.waitFor(() => expect(state.scrollTop).toBe(1200))
    expect(screen.queryByRole('button', { name: 'Jump to latest' })).not.toBeInTheDocument()
  })

  it('shows Jump to latest only after scrolling up and new content arriving, and jumps', async () => {
    const { rerender } = render(<ChatThread>one</ChatThread>)
    const log = screen.getByRole('log')
    const state = geometry(log, { scrollHeight: 1000, clientHeight: 200, scrollTop: 800 })
    state.scrollTop = 100 // the reader scrolled up
    fireEvent.scroll(log)
    expect(screen.queryByRole('button', { name: 'Jump to latest' })).not.toBeInTheDocument()
    state.scrollHeight = 1300
    await act(async () => {
      rerender(
        <ChatThread>
          one<p>new</p>
        </ChatThread>,
      )
    })
    const jump = await screen.findByRole('button', { name: 'Jump to latest' })
    expect(state.scrollTop).toBe(100) // it did not yank the reader down
    await userEvent.click(jump)
    expect(state.scrollTop).toBe(1300)
    expect(screen.queryByRole('button', { name: 'Jump to latest' })).not.toBeInTheDocument()
  })

  it('hides Jump to latest when the reader scrolls back to the bottom', async () => {
    const { rerender } = render(<ChatThread>one</ChatThread>)
    const log = screen.getByRole('log')
    const state = geometry(log, { scrollHeight: 1000, clientHeight: 200, scrollTop: 100 })
    fireEvent.scroll(log)
    state.scrollHeight = 1300
    await act(async () => {
      rerender(
        <ChatThread>
          one<p>new</p>
        </ChatThread>,
      )
    })
    await screen.findByRole('button', { name: 'Jump to latest' })
    state.scrollTop = 1100
    fireEvent.scroll(log)
    expect(screen.queryByRole('button', { name: 'Jump to latest' })).not.toBeInTheDocument()
  })
})
