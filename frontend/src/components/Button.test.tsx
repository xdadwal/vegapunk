import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { Button } from './Button'

describe('Button', () => {
  it('is a plain button that forwards clicks', async () => {
    const onClick = vi.fn()
    render(<Button onClick={onClick}>Save details</Button>)
    await userEvent.click(screen.getByRole('button', { name: 'Save details' }))
    expect(onClick).toHaveBeenCalledOnce()
    expect(screen.getByRole('button')).toHaveAttribute('type', 'button')
  })

  it('is disabled and busy while loading', async () => {
    const onClick = vi.fn()
    render(
      <Button loading onClick={onClick}>
        Saving…
      </Button>,
    )
    const button = screen.getByRole('button', { name: /Saving/ })
    expect(button).toBeDisabled()
    expect(button).toHaveAttribute('aria-busy', 'true')
    await userEvent.click(button)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('applies the variant and size classes', () => {
    render(
      <Button variant="primary" size="sm">
        Go
      </Button>,
    )
    expect(screen.getByRole('button')).toHaveClass('vp-btn', 'vp-btn-primary', 'vp-btn-sm')
  })
})
