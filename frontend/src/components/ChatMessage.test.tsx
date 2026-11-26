import { render, screen } from '@testing-library/react'

import { ChatMessage } from './ChatMessage'

describe('ChatMessage', () => {
  it('shows a user message as "You"', () => {
    render(<ChatMessage role="user" text="Is anything wrong?" />)
    const message = screen.getByRole('article', { name: 'You' })
    expect(message).toHaveTextContent('Is anything wrong?')
  })

  it('shows an assistant message with its name, "Agent" by default', () => {
    const { rerender } = render(<ChatMessage role="assistant" text="All good." />)
    expect(screen.getByRole('article', { name: 'Agent' })).toHaveTextContent('All good.')
    rerender(<ChatMessage role="assistant" text="All good." name="Atlas" />)
    expect(screen.getByRole('article', { name: 'Atlas' })).toBeInTheDocument()
  })

  it('shows each step with a spoken status', () => {
    render(
      <ChatMessage
        role="assistant"
        text="Done."
        steps={[
          { label: 'Read the status of the containers', status: 'done' },
          { label: 'Read the last 100 log lines of `web`', status: 'running' },
          { label: 'Read the details of `db`', status: 'failed' },
        ]}
      />,
    )
    const steps = screen.getByRole('list', { name: 'Steps' })
    const items = steps.querySelectorAll('li')
    expect(items).toHaveLength(3)
    expect(items[0]).toHaveTextContent('Done: Read the status of the containers')
    expect(items[1]).toHaveTextContent('Running: Read the last 100 log lines of web')
    expect(items[2]).toHaveTextContent('Failed: Read the details of db')
    expect(screen.getByText('web').tagName).toBe('CODE')
    expect(items[0].querySelector('.vp-sr-only')).toHaveTextContent('Done:')
  })

  it('shows Thinking… only while thinking with no text', () => {
    const { rerender } = render(<ChatMessage role="assistant" text="" status="thinking" />)
    expect(screen.getByText('Thinking…')).toBeVisible()
    rerender(<ChatMessage role="assistant" text="Here is" status="thinking" />)
    expect(screen.queryByText('Thinking…')).not.toBeInTheDocument()
    rerender(<ChatMessage role="assistant" text="" />)
    expect(screen.queryByText('Thinking…')).not.toBeInTheDocument()
  })

  it('marks itself busy while thinking or streaming, and shows a caret only when streaming text', () => {
    const { container, rerender } = render(<ChatMessage role="assistant" text="Part" status="streaming" />)
    expect(screen.getByRole('article', { name: 'Agent' })).toHaveAttribute('aria-busy', 'true')
    expect(container.querySelector('.vp-caret')).not.toBeNull()
    rerender(<ChatMessage role="assistant" text="" status="thinking" />)
    expect(screen.getByRole('article', { name: 'Agent' })).toHaveAttribute('aria-busy', 'true')
    expect(container.querySelector('.vp-caret')).toBeNull()
    rerender(<ChatMessage role="assistant" text="Done" />)
    expect(screen.getByRole('article', { name: 'Agent' })).not.toHaveAttribute('aria-busy')
    expect(container.querySelector('.vp-caret')).toBeNull()
  })

  it('puts the caret at the end of the text, not on its own line', () => {
    const { container } = render(<ChatMessage role="assistant" text={'One.\n\nTwo'} status="streaming" />)
    const paragraphs = container.querySelectorAll('p')
    expect(paragraphs[1].querySelector('.vp-caret')).not.toBeNull()
    expect(paragraphs[0].querySelector('.vp-caret')).toBeNull()
  })

  it('shows model output as text, never markup', () => {
    const { container } = render(<ChatMessage role="assistant" text="<img src=x onerror=alert(1)>" />)
    expect(container.querySelector('img')).toBeNull()
    expect(container).toHaveTextContent('<img src=x onerror=alert(1)>')
  })

  it('shows no steps list when there are none', () => {
    render(<ChatMessage role="assistant" text="Hi" steps={[]} />)
    expect(screen.queryByRole('list', { name: 'Steps' })).not.toBeInTheDocument()
  })
})
