import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { Pagination } from './Pagination'

describe('Pagination', () => {
  it('shows the count and moves between pages', async () => {
    const onPage = vi.fn()
    render(<Pagination page={0} pageSize={10} total={25} noun="finding" onPage={onPage} />)
    expect(screen.getByText('25 findings match')).toHaveAttribute('aria-live', 'polite')
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(onPage).toHaveBeenCalledWith(1)
  })

  it('disables Next on the last page and uses the singular', () => {
    render(<Pagination page={2} pageSize={10} total={21} noun="finding" onPage={() => {}} />)
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled()
  })

  it('says "1 finding matches"', () => {
    render(<Pagination page={0} pageSize={10} total={1} noun="finding" onPage={() => {}} />)
    expect(screen.getByText('1 finding matches')).toBeVisible()
  })
})
