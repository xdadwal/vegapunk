import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { findingDetail } from '../test-fixtures'
import { ADMIN, renderApp, stubApi } from '../test-utils'

function page(routes: Parameters<typeof stubApi>[0]) {
  return stubApi({
    'GET /setup/status': { body: { admin_created: true } },
    'GET /me': { body: ADMIN },
    ...routes,
  })
}

describe('Finding detail', () => {
  it('shows the header, the three sections and the stored text', async () => {
    page({ 'GET /findings/f1': { body: findingDetail() } })
    renderApp('/findings/f1')
    expect(await screen.findByRole('heading', { level: 1, name: 'web keeps restarting' })).toBeVisible()
    expect(screen.getByText('Needs you')).toBeVisible()
    const crumbs = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(within(crumbs).getByRole('link', { name: 'Findings' })).toHaveAttribute('href', '/findings')
    expect(within(crumbs).getByText('web · web')).toBeVisible()
    expect(
      screen.getByText(/^on web · since 3:00 PM UTC · Found by background watch$/),
    ).toBeVisible()
    for (const name of ['Diagnosis', 'Evidence', 'Suggested fix']) {
      expect(screen.getByRole('heading', { level: 2, name })).toBeVisible()
    }
    expect(screen.getByText('The web container exits with code 137 every few minutes.')).toBeVisible()
    expect(screen.getByText('Raise the memory limit to 1 GiB.')).toBeVisible()
    expect(screen.getByText('Written by the agent from the logs. Check it before acting on it.')).toBeVisible()
    expect(screen.getByText("Vegapunk doesn't change anything on your host.")).toBeVisible()
    expect(screen.getByText('docker logs web · times in UTC')).toBeVisible()
    expect(screen.getByText('fatal: out of memory')).toBeVisible()
    expect(screen.getByText('1:48:02 PM')).toBeVisible()
  })

  it('titles the evidence as docker logs when a log line follows a metric line', async () => {
    page({
      'GET /findings/f1': {
        body: findingDetail({
          evidence: [
            { source: 'metric', text: 'Memory at 98%', at: null },
            { source: 'log', text: 'fatal', at: null },
          ],
        }),
      },
    })
    renderApp('/findings/f1')
    expect(await screen.findByText('docker logs web · times in UTC')).toBeVisible()
  })

  it('survives malformed timestamps', async () => {
    page({
      'GET /findings/f1': {
        body: findingDetail({
          first_seen_at: 'garbage',
          status: 'resolved',
          resolved_at: 'also bad',
          evidence: [{ source: 'log', text: 'x', at: 'not a date' }],
        }),
      },
    })
    renderApp('/findings/f1')
    expect(await screen.findByText(/since garbage/)).toBeVisible()
    expect(screen.getByText('This stopped on its own at also bad.')).toBeVisible()
  })

  it('names a scheduled check', async () => {
    page({ 'GET /findings/f1': { body: findingDetail({ trigger: 'scheduled' }) } })
    renderApp('/findings/f1')
    expect(await screen.findByText(/Found by scheduled check$/)).toBeVisible()
  })

  it('shows evidence text literally', async () => {
    page({
      'GET /findings/f1': {
        body: findingDetail({ evidence: [{ source: 'log', text: '<b>x</b>', at: null }] }),
      },
    })
    renderApp('/findings/f1')
    expect(await screen.findByText('<b>x</b>')).toBeVisible()
  })

  it('copes with no evidence and no fix', async () => {
    page({ 'GET /findings/f1': { body: findingDetail({ evidence: [], suggested_fix: '' }) } })
    renderApp('/findings/f1')
    expect(await screen.findByText('No evidence lines were kept.')).toBeVisible()
    expect(screen.getByText("The agent didn't suggest a fix.")).toBeVisible()
  })

  it('passes an encoded id to the API', async () => {
    const { fetchMock } = page({ 'GET /findings/a%2Fb': { body: findingDetail({ id: 'a/b' }) } })
    renderApp('/findings/a%2Fb')
    expect(await screen.findByRole('link', { name: 'Ask agent about this finding' })).toBeVisible()
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/findings/a%2Fb')).toBe(true)
    expect(screen.getByRole('link', { name: 'Ask agent about this finding' })).toHaveAttribute(
      'href',
      '/agent?about=a%2Fb',
    )
  })

  it('marks an open finding as reviewed, flips the badge and hides the button', async () => {
    const { sent } = page({
      'GET /findings/f1': { body: findingDetail() },
      'POST /findings/f1/review': { status: 204 },
    })
    renderApp('/findings/f1')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Mark as reviewed' }))
    expect(await screen.findByText('Reviewed')).toBeVisible()
    expect(screen.queryByText('Needs you')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Mark as reviewed' })).not.toBeInTheDocument()
    expect(sent('POST /findings/f1/review')).toHaveLength(1)
  })

  it('shows an alert and keeps the button when marking fails', async () => {
    page({
      'GET /findings/f1': { body: findingDetail() },
      'POST /findings/f1/review': { status: 500, body: { detail: 'Disk full.' } },
    })
    renderApp('/findings/f1')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Mark as reviewed' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Disk full.')
    expect(screen.getByRole('button', { name: 'Mark as reviewed' })).toBeEnabled()
    expect(screen.getByText('Needs you')).toBeVisible()
  })

  it('offers no review for resolved or reviewed findings, and says when a resolved one stopped', async () => {
    page({
      'GET /findings/f1': {
        body: findingDetail({ status: 'resolved', resolved_at: '2026-11-26T16:30:00Z' }),
      },
    })
    renderApp('/findings/f1')
    expect(await screen.findByText('Resolved')).toBeVisible()
    expect(screen.getByText('This stopped on its own at 4:30 PM UTC.')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Mark as reviewed' })).not.toBeInTheDocument()
  })

  it('shows the reviewed badge with no review button', async () => {
    page({ 'GET /findings/f1': { body: findingDetail({ status: 'reviewed' }) } })
    renderApp('/findings/f1')
    expect(await screen.findByText('Reviewed')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Mark as reviewed' })).not.toBeInTheDocument()
  })

  it('has the Ask link with the right href', async () => {
    page({ 'GET /findings/f1': { body: findingDetail() } })
    renderApp('/findings/f1')
    const ask = await screen.findByRole('link', { name: 'Ask agent about this finding' })
    expect(ask).toHaveAttribute('href', '/agent?about=f1')
    expect(ask).toHaveClass('vp-btn', 'vp-btn-secondary')
  })

  it('offers to continue the conversation already started about it', async () => {
    const cid = 'c/d'
    page({ 'GET /findings/f1': { body: findingDetail({ conversation_id: cid }) } })
    renderApp('/findings/f1')
    const go = await screen.findByRole('link', { name: 'Continue with the agent' })
    expect(go).toHaveAttribute('href', '/agent?c=c%2Fd')
    expect(go).toHaveClass('vp-btn', 'vp-btn-secondary')
    expect(screen.getByText("You've already asked the agent about this finding.")).toHaveClass('page-note')
    expect(screen.queryByRole('link', { name: 'Ask agent about this finding' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Mark as reviewed' })).toBeVisible()
  })

  it('does not say it was asked about when no conversation exists', async () => {
    page({ 'GET /findings/f1': { body: findingDetail() } })
    renderApp('/findings/f1')
    await screen.findByRole('link', { name: 'Ask agent about this finding' })
    expect(screen.queryByRole('link', { name: 'Continue with the agent' })).not.toBeInTheDocument()
    expect(screen.queryByText("You've already asked the agent about this finding.")).not.toBeInTheDocument()
  })

  it('puts the breadcrumb, the Ask link and Mark as reviewed in that focus order', async () => {
    page({ 'GET /findings/f1': { body: findingDetail() } })
    renderApp('/findings/f1')
    await screen.findByRole('heading', { level: 1, name: 'web keeps restarting' })
    const ask = screen.getByRole('link', { name: 'Ask agent about this finding' })
    const mark = screen.getByRole('button', { name: 'Mark as reviewed' })
    expect(ask.compareDocumentPosition(mark) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const crumb = within(screen.getByRole('navigation', { name: 'Breadcrumb' })).getByRole('link', { name: 'Findings' })
    expect(crumb.compareDocumentPosition(ask) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('says the finding no longer exists on a 404, with a link back', async () => {
    page({ 'GET /findings/nope': { status: 404, body: { detail: 'No such finding.' } } })
    renderApp('/findings/nope')
    expect(await screen.findByText('This finding no longer exists')).toBeVisible()
    expect(screen.getByRole('link', { name: 'Back to findings' })).toHaveAttribute('href', '/findings')
  })

  it('offers a retry when loading fails', async () => {
    let calls = 0
    page({
      'GET /findings/f1': () => (++calls === 1 ? { status: 500, body: { detail: 'Boom.' } } : { body: findingDetail() }),
    })
    renderApp('/findings/f1')
    const user = userEvent.setup()
    expect(await screen.findByText("Couldn't load this finding")).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'web keeps restarting' })).toBeVisible()
  })
})
