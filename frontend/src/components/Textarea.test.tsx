import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { Textarea } from './Textarea'

describe('Textarea', () => {
  it('ties its label to the field, and hides the label visually when asked', () => {
    const { rerender } = render(<Textarea label="Message the agent" />)
    expect(screen.getByLabelText('Message the agent')).toBeInTheDocument()
    expect(screen.getByText('Message the agent')).not.toHaveClass('vp-sr-only')
    rerender(<Textarea label="Message the agent" labelHidden />)
    expect(screen.getByLabelText('Message the agent')).toBeInTheDocument()
    expect(screen.getByText('Message the agent')).toHaveClass('vp-sr-only')
  })

  it('Enter submits, Shift+Enter adds a line', async () => {
    const onSubmit = vi.fn()
    render(<Textarea label="Message" submitOnEnter onSubmit={onSubmit} />)
    const field = screen.getByLabelText('Message')
    await userEvent.type(field, 'hello{Shift>}{Enter}{/Shift}world')
    expect(onSubmit).not.toHaveBeenCalled()
    expect(field).toHaveValue('hello\nworld')
    await userEvent.type(field, '{Enter}')
    expect(onSubmit).toHaveBeenCalledWith('hello\nworld')
  })

  it('does nothing on Enter while an input method is composing', () => {
    const onSubmit = vi.fn()
    render(<Textarea label="Message" submitOnEnter onSubmit={onSubmit} />)
    fireEvent.keyDown(screen.getByLabelText('Message'), { key: 'Enter', isComposing: true })
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('does not submit on Enter without submitOnEnter', async () => {
    const onSubmit = vi.fn()
    render(<Textarea label="Message" onSubmit={onSubmit} />)
    await userEvent.type(screen.getByLabelText('Message'), 'a{Enter}b')
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('keeps Ctrl, Alt and Meta with Enter from submitting', () => {
    const onSubmit = vi.fn()
    render(<Textarea label="Message" submitOnEnter onSubmit={onSubmit} />)
    const field = screen.getByLabelText('Message')
    for (const modifier of ['ctrlKey', 'altKey', 'metaKey'])
      fireEvent.keyDown(field, { key: 'Enter', [modifier]: true })
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('grows with its content up to maxHeight, then scrolls', () => {
    render(<Textarea label="Message" maxHeight={200} />)
    const field = screen.getByLabelText('Message') as HTMLTextAreaElement
    Object.defineProperty(field, 'scrollHeight', { value: 120, configurable: true })
    fireEvent.input(field)
    expect(field.style.height).toBe('122px')
    expect(field.style.overflowY).toBe('hidden')
    Object.defineProperty(field, 'scrollHeight', { value: 500, configurable: true })
    fireEvent.input(field)
    expect(field.style.height).toBe('200px')
    expect(field.style.overflowY).toBe('auto')
  })

  it('shows its hint, and the error instead of it', () => {
    const { rerender } = render(<Textarea label="Notes" hint="Enter sends." />)
    expect(screen.getByLabelText('Notes')).toHaveAccessibleDescription('Enter sends.')
    rerender(<Textarea label="Notes" hint="Enter sends." error="Too long." />)
    expect(screen.getByLabelText('Notes')).toHaveAccessibleDescription('Too long.')
    expect(screen.getByLabelText('Notes')).toHaveAttribute('aria-invalid', 'true')
  })
})
