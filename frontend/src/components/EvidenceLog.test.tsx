import { render, screen, within } from '@testing-library/react'

import { EvidenceLog } from './EvidenceLog'

describe('EvidenceLog', () => {
  it('shows a time column in 12-hour UTC when a line has a time', () => {
    render(
      <EvidenceLog
        lines={[
          { source: 'log', text: 'boom', at: '2026-11-26T13:48:02Z' },
          { source: 'log', text: 'no time here', at: null },
        ]}
      />,
    )
    const list = screen.getByRole('list', { name: 'Evidence' })
    const rows = within(list).getAllByRole('listitem')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('1:48:02 PM')
    expect(rows[0]).toHaveTextContent('boom')
    expect(rows[1].querySelector('.evidence-time')).toBeEmptyDOMElement()
  })

  it('has no time cells when no line has a time', () => {
    render(<EvidenceLog lines={[{ source: 'log', text: 'boom', at: null }]} />)
    expect(screen.getByRole('listitem').querySelector('.evidence-time')).toBeNull()
  })

  it('labels a metric line as a note', () => {
    render(<EvidenceLog lines={[{ source: 'metric', text: 'Memory at 98%', at: null }]} />)
    const row = screen.getByRole('listitem')
    expect(row).toHaveTextContent('Measured')
    expect(row).toHaveTextContent('Memory at 98%')
    expect(row).toHaveClass('evidence-note')
  })

  it('shows text literally, never as HTML', () => {
    render(<EvidenceLog lines={[{ source: 'log', text: '<b>x</b>', at: null }]} />)
    expect(screen.getByText('<b>x</b>')).toBeVisible()
    expect(document.querySelector('b')).toBeNull()
  })

  it('says so when there are no lines', () => {
    render(<EvidenceLog lines={[]} />)
    expect(screen.getByText('No evidence lines were kept.')).toBeVisible()
    expect(screen.queryByRole('list')).not.toBeInTheDocument()
  })

  it('shows the title above the block and names the list after it', () => {
    render(<EvidenceLog title="docker logs web · times in UTC" lines={[{ source: 'log', text: 'x', at: null }]} />)
    expect(screen.getByText('docker logs web · times in UTC')).toBeVisible()
    expect(screen.getByRole('list', { name: 'docker logs web · times in UTC' })).toBeInTheDocument()
  })
})
