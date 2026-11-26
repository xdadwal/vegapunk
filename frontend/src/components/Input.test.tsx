import { render, screen } from '@testing-library/react'

import { Input } from './Input'

describe('Input', () => {
  it('ties the label to the field', () => {
    render(<Input label="Username" />)
    expect(screen.getByLabelText('Username')).toBeInTheDocument()
  })

  it('describes the field with its hint', () => {
    render(<Input label="Password" hint="At least 10 characters." />)
    expect(screen.getByLabelText('Password')).toHaveAccessibleDescription('At least 10 characters.')
  })

  it('shows the error instead of the hint and marks the field invalid', () => {
    render(<Input label="Password" hint="At least 10 characters." error="Too short." />)
    const field = screen.getByLabelText('Password')
    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(field).toHaveAccessibleDescription('Too short.')
  })

  it('gives two inputs different ids', () => {
    render(
      <>
        <Input label="One" />
        <Input label="Two" />
      </>,
    )
    expect(screen.getByLabelText('One').id).not.toBe(screen.getByLabelText('Two').id)
  })

  it('uses the mono style when asked', () => {
    render(<Input label="Command" mono />)
    expect(screen.getByLabelText('Command')).toHaveClass('vp-input-mono')
  })
})
