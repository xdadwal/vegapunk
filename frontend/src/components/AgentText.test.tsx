import { render, screen } from '@testing-library/react'

import { AgentText } from './AgentText'

describe('AgentText', () => {
  it('makes paragraphs', () => {
    const { container } = render(<AgentText text={'First paragraph.\n\nSecond paragraph.'} />)
    expect(container.querySelectorAll('p')).toHaveLength(2)
  })

  it('keeps single line breaks inside a paragraph', () => {
    const { container } = render(<AgentText text={'line one\nline two'} />)
    expect(container.querySelectorAll('p')).toHaveLength(1)
    expect(container.querySelectorAll('br')).toHaveLength(1)
  })

  it('makes bullet lists and numbered lists', () => {
    const { container } = render(<AgentText text={'- one\n- two\n\n1. first\n2) second'} />)
    expect(container.querySelectorAll('ul > li')).toHaveLength(2)
    expect(container.querySelectorAll('ol > li')).toHaveLength(2)
    expect(screen.getByText('second')).toBeVisible()
  })

  it('sets backticked text in mono', () => {
    render(<AgentText text="Run `docker ps` on `nas-01`." />)
    expect(screen.getByText('docker ps')).toHaveClass('vp-mono')
    expect(screen.getByText('docker ps').tagName).toBe('CODE')
    expect(screen.getByText('nas-01').tagName).toBe('CODE')
  })

  it('never renders markup', () => {
    const attack = '<img src=x onerror=alert(1)> and <script>alert(2)</script> <b>bold</b>'
    const { container } = render(<AgentText text={attack} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
    expect(container.textContent).toBe(attack)
  })

  it('never renders markup inside code, lists or steps either', () => {
    const { container } = render(<AgentText text={'- <img src=x onerror=1>\n- `<script>x</script>`'} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('code')?.textContent).toBe('<script>x</script>')
  })

  it('keeps a stray or unbalanced backtick as plain text', () => {
    for (const text of ['one ` two', 'a `c d', 'a `` b', 'trailing `']) {
      const { container, unmount } = render(<AgentText text={text} />)
      expect(container.querySelector('code'), text).toBeNull()
      expect(container.textContent).toBe(text)
      unmount()
    }
  })

  it('handles empty and whitespace-only text', () => {
    expect(render(<AgentText text="" />).container).toBeEmptyDOMElement()
    expect(render(<AgentText text={'  \n\n  '} />).container).toBeEmptyDOMElement()
  })

  it('handles Windows line endings', () => {
    const { container } = render(<AgentText text={'a\r\n\r\nb'} />)
    expect(container.querySelectorAll('p')).toHaveLength(2)
  })

  it('handles a very long unbroken line', () => {
    const { container } = render(<AgentText text={'x'.repeat(50_000)} />)
    expect(container.textContent).toHaveLength(50_000)
  })
})
