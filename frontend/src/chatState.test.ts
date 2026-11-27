import { applyEvent, finish, fromStored } from './chatState'
import type { Msg } from './chatState'

const waiting: Msg[] = [
  { role: 'user', text: 'hi', steps: [] },
  { role: 'assistant', text: '', steps: [], status: 'thinking' },
]

describe('applyEvent', () => {
  it('appends text to the last assistant message and marks it streaming', () => {
    let m = applyEvent(waiting, { type: 'text', text: 'Hel' })
    m = applyEvent(m, { type: 'text', text: 'lo' })
    expect(m[1]).toMatchObject({ text: 'Hello', status: 'streaming' })
    expect(m[0]).toEqual(waiting[0])
  })

  it('adds a running step, then updates it in place when it finishes', () => {
    let m = applyEvent(waiting, { type: 'step', id: 's1', label: 'Read `web`', status: 'running' })
    expect(m[1].steps).toEqual([{ id: 's1', label: 'Read `web`', status: 'running' }])
    expect(m[1].status).toBe('thinking')
    m = applyEvent(m, { type: 'step', id: 's1', label: 'Read `web`', status: 'done' })
    expect(m[1].steps).toEqual([{ id: 's1', label: 'Read `web`', status: 'done' }])
  })

  it('keeps several steps in order', () => {
    let m = applyEvent(waiting, { type: 'step', id: 'a', label: 'A', status: 'running' })
    m = applyEvent(m, { type: 'step', id: 'b', label: 'B', status: 'running' })
    m = applyEvent(m, { type: 'step', id: 'a', label: 'A', status: 'failed' })
    expect(m[1].steps.map((s) => [s.id, s.status])).toEqual([
      ['a', 'failed'],
      ['b', 'running'],
    ])
  })

  it('finishes on done', () => {
    const m = applyEvent(applyEvent(waiting, { type: 'text', text: 'x' }), {
      type: 'done',
      usage: { input_tokens: 1, output_tokens: 1 },
    })
    expect(m[1].status).toBeUndefined()
  })

  it('records an error and stops waiting', () => {
    const m = applyEvent(waiting, { type: 'error', code: 'timeout', message: 'The agent took too long to answer.' })
    expect(m[1]).toMatchObject({ error: 'The agent took too long to answer.' })
    expect(m[1].status).toBeUndefined()
  })

  it('marks a stopped answer as stopped, not as failed, keeping what was written', () => {
    const partial = applyEvent(waiting, { type: 'text', text: 'Partly' })
    const m = applyEvent(partial, { type: 'error', code: 'stopped', message: 'Stopped.' })
    expect(m[1]).toMatchObject({ text: 'Partly', stopped: true })
    expect(m[1].error).toBeUndefined()
    expect(m[1].status).toBeUndefined()
  })

  it('leaves an empty stopped answer empty, so finish does not add Stopped. as its text', () => {
    const m = finish(applyEvent(waiting, { type: 'error', code: 'stopped', message: 'Stopped.' }))
    expect(m[1]).toMatchObject({ text: '', stopped: true })
  })

  it('ignores the conversation event and does not change its input', () => {
    const before = JSON.stringify(waiting)
    expect(applyEvent(waiting, { type: 'conversation', id: 'c', title: 't', finding_id: null })).toEqual(waiting)
    applyEvent(waiting, { type: 'text', text: 'x' })
    expect(JSON.stringify(waiting)).toBe(before)
  })

  it('only changes the last message, and does nothing when there is no assistant message', () => {
    const earlier: Msg[] = [{ role: 'assistant', text: 'old', steps: [] }, ...waiting]
    expect(applyEvent(earlier, { type: 'text', text: '!' })[0].text).toBe('old')
    const lone: Msg[] = [{ role: 'user', text: 'hi', steps: [] }]
    expect(applyEvent(lone, { type: 'text', text: 'x' })).toEqual(lone)
    expect(applyEvent([], { type: 'text', text: 'x' })).toEqual([])
  })
})

describe('finish', () => {
  it('clears the waiting state of the last message and marks an empty answer as stopped', () => {
    expect(finish(waiting)[1]).toMatchObject({ text: 'Stopped.' })
    expect(finish(waiting)[1].status).toBeUndefined()
  })

  it('keeps partial text as it is', () => {
    const partial = applyEvent(waiting, { type: 'text', text: 'Partly' })
    expect(finish(partial)[1]).toMatchObject({ text: 'Partly' })
    expect(finish(partial)[1].status).toBeUndefined()
  })

  it('does not call an answer that only had steps or an error stopped', () => {
    const withError = applyEvent(waiting, { type: 'error', code: 'x', message: 'Bad.' })
    expect(finish(withError)[1].text).toBe('')
  })

  it('does not call an answer stopped when it had already taken steps', () => {
    const steps = applyEvent(waiting, { type: 'step', id: 's', label: 'Read it', status: 'running' })
    const done = finish(steps)
    expect(done[1].text).toBe('')
    expect(done[1].steps).toHaveLength(1)
    expect(done[1].status).toBeUndefined()
  })

  it('does nothing to a conversation that is not waiting', () => {
    const settled: Msg[] = [
      { role: 'user', text: 'a', steps: [] },
      { role: 'assistant', text: 'b', steps: [] },
    ]
    expect(finish(settled)).toEqual(settled)
  })
})

describe('fromStored', () => {
  it('turns stored messages into the page state, steps with their statuses', () => {
    expect(
      fromStored([
        { role: 'user', text: 'q', steps: [] },
        { role: 'assistant', text: 'a', steps: [{ label: 'Read it', status: 'failed' }] },
      ]),
    ).toEqual([
      { role: 'user', text: 'q', steps: [] },
      { role: 'assistant', text: 'a', steps: [{ label: 'Read it', status: 'failed' }] },
    ])
  })
})
