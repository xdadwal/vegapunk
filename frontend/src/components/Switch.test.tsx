import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { Switch } from './Switch'

describe('Switch', () => {
  it('is a labelled switch that reports the new state on click and on the label', async () => {
    const onChange = vi.fn()
    render(<Switch label="Run a check every day" checked={false} onChange={onChange} />)
    const sw = screen.getByRole('switch', { name: 'Run a check every day' })
    expect(sw).toHaveAttribute('aria-checked', 'false')
    await userEvent.click(sw)
    await userEvent.click(screen.getByText('Run a check every day'))
    expect(onChange).toHaveBeenNthCalledWith(1, true)
    expect(onChange).toHaveBeenNthCalledWith(2, true)
  })

  it('reflects checked and can be disabled', () => {
    render(<Switch label="On" checked onChange={() => {}} disabled />)
    expect(screen.getByRole('switch')).toBeChecked()
    expect(screen.getByRole('switch')).toBeDisabled()
  })
})
