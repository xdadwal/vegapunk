import type { ReactNode } from 'react'

/** A backtick pair around text is code; anything else, stray backticks included, is text. */
function inline(text: string, keyPrefix: string): ReactNode[] {
  return text.split(/(`[^`\n]+`)/).map((part, i) =>
    part.length > 2 && part.startsWith('`') && part.endsWith('`') ? (
      <code key={`${keyPrefix}${i}`} className="vp-mono">
        {part.slice(1, -1)}
      </code>
    ) : (
      part
    ),
  )
}

/** One line of model text with `code` set in mono. React text, never HTML. */
export function AgentInline({ text }: { text: string }) {
  return <>{inline(text, 'i')}</>
}

type Block = { kind: 'p' | 'ul' | 'ol'; lines: string[] }

function blocksOf(text: string): Block[] {
  return text
    .replace(/\r\n/g, '\n')
    .split(/\n{2,}/)
    .map((block): Block | null => {
      const lines = block.split('\n').filter((line) => line.trim() !== '')
      if (lines.length === 0) return null
      if (lines.every((line) => /^\s*[-*] /.test(line)))
        return { kind: 'ul', lines: lines.map((line) => line.replace(/^\s*[-*] /, '')) }
      if (lines.every((line) => /^\s*\d+[.)] /.test(line)))
        return { kind: 'ol', lines: lines.map((line) => line.replace(/^\s*\d+[.)] /, '')) }
      return { kind: 'p', lines }
    })
    .filter((block): block is Block => block !== null)
}

/**
 * The model's plain text as safe React elements: blank lines make paragraphs, `- ` lines a
 * list, `1. ` lines a numbered list, and `backticks` mono. It is never parsed as HTML, so a
 * log line cannot inject markup. With `caret`, a streaming caret sits at the end of the
 * last paragraph or list item, never on a line of its own.
 */
export function AgentText({ text, caret = false }: { text: string; caret?: boolean }) {
  const blocks = blocksOf(text)
  const tail = <span className="vp-caret" aria-hidden="true" />
  return (
    <>
      {blocks.map((block, b) => {
        const isLast = b === blocks.length - 1
        if (block.kind === 'p')
          return (
            <p key={b}>
              {block.lines.map((line, l) => (
                <span key={l}>
                  {l > 0 && <br />}
                  {inline(line, `${b}-${l}-`)}
                </span>
              ))}
              {caret && isLast && tail}
            </p>
          )
        const List = block.kind
        return (
          <List key={b}>
            {block.lines.map((line, l) => (
              <li key={l}>
                {inline(line, `${b}-${l}-`)}
                {caret && isLast && l === block.lines.length - 1 && tail}
              </li>
            ))}
          </List>
        )
      })}
    </>
  )
}
