import { render, screen } from '@testing-library/react'

import { Alert } from './Alert'

describe('Alert', () => {
  it('announces danger alerts', () => {
    render(
      <Alert tone="danger" title="Couldn't load Vegapunk">
        The server didn't respond.
      </Alert>,
    )
    expect(screen.getByRole('alert')).toHaveTextContent("Couldn't load Vegapunk")
    expect(screen.getByRole('alert')).toHaveTextContent("The server didn't respond.")
  })

  it('does not interrupt for success alerts', () => {
    render(<Alert tone="success" title="Details saved" />)
    expect(screen.getByRole('status')).toHaveTextContent('Details saved')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('renders its action', () => {
    render(<Alert tone="danger" title="Failed" action={<button>Retry</button>} />)
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
