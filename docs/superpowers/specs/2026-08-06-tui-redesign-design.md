# Vegapunk TUI redesign

Status: approved design, not yet implemented.
Date: 2026-08-06.

## The problem

Vegapunk's output is functional and unstyled. A real turn, captured with colour
forced on:

```
  [think] step 1
  [tool] list_dir({'path': '.'}) -> requirements.txt
scheduler.log
…  (+168 more chars)
  [think] step 2
  [reason] I'm counting through the files and directories…
vega> Here's what's in the workspace root:

**Directories (12)**
- `.agents/`, `.claude/`, `.git/`, `.venv/`
…
**Total: 29 entries** — 12 directories and 17 files.
```

Three concrete faults, in order of how loudly they read:

1. **Markdown is printed literally.** `**Directories (12)**` and backticked
   paths arrive as asterisks and backticks. Every comparable CLI agent renders
   them.
2. **Multi-line tool results break the trace.** `list_dir` dumped raw newlines
   into the middle of a `[tool]` line, so the trace stops being one line per
   event exactly when there is most to read.
3. **No boundary between turns.** Trace, reply and the next prompt run together.

None of these need a full-screen application to fix.

A fourth was suspected and disproved: the capture above appears to mangle `…`,
but that is `cat -v` rendering non-ASCII in the capture command, not a defect.
`_shorten` slices a `str`, and Python slices by codepoint, so truncation is
already multi-byte safe. Verified before this spec was written; no work item.

## Decisions taken

Settled during the design conversation; each closed a fork that would have
changed the shape of the work.

1. **Live re-render while streaming.** Token-by-token liveness *and* rendered
   markdown, by re-rendering the in-progress block in place. Not "stream raw
   then redraw once" (visible glitch), and not "render only at the end" (loses a
   headline feature).
2. **The stdout/stderr split stays.** Replies on stdout, trace on stderr. A live
   region and an independent stderr writer would corrupt each other, so the
   renderer finalises the current live block before any trace line prints and
   opens a fresh one afterwards.
3. **Glyph markers plus a reply gutter** as the visual language, shared by the
   trace, the reply, and the selectors.
4. **Tree-glyph trace**, one line per event, with multi-line results indented
   beneath their call.
5. **No full-screen multi-pane app.** See Non-goals.
6. **Keep the spinner**; add a live status tail to it rather than replacing it.
7. **Reasoning is not displayed at all.** The spinner shows that thinking is
   streaming; its content never reaches the screen. This replaces an earlier
   collapse-and-expand design, and removes the `/reason` command it needed.

## Architecture

### The renderer seam

New module `vegapunk/render.py`. A `Renderer` protocol with two
implementations, chosen at construction:

- **`RichRenderer`** — live markdown, glyph trace, gutter, live status. Used
  when the stream is a terminal.
- **`PlainRenderer`** — today's bytes: `[think] step N`, `[tool] name(args) ->
  result`. Used when output is piped, under `NO_COLOR`, under
  `VEGAPUNK_UI=plain`, and throughout the test suite. Only the `[reason]` line
  changes (it goes away — see Reasoning), so the existing suite stands as the
  regression check for everything else.

The seam is the same one `style.enabled()` already applies per stream, and it
earns its place three ways:

- `2>trace.log` and `| less -R` keep working, which is a documented feature.
- The existing suite keeps asserting the plain output it already pins, so a
  redesign of the presentation does not churn ~35 display tests.
- The rich path becomes testable in isolation against a captured console.

### Where rendering is called from

`loop.trace(events)` gains a `renderer` parameter and calls
`renderer.tool_result(...)` / `renderer.reasoning(...)` in place of `print`.
The function stays a pure consumer of logpose's event stream — the boundary
established in PR #31 is preserved, and `loop.py` still knows nothing about
`Agent`.

`cli._render_reply` delegates the reply body to the same renderer instead of
printing `TextDelta`s directly.

### Live-region ownership

`RichRenderer` opens a `rich.live.Live` for each contiguous run of text deltas
and finalises it when a `ToolCall` arrives, opening a fresh one for the next
run. `loop.py` already tracks precisely this boundary (`in_tool_phase`), so no
new state is introduced. This is what keeps decision 2 true.

**Verified before this spec was approved**, against `rich` in a throwaway venv:

- `Markdown` renders and captures deterministically through
  `Console(file=StringIO, force_terminal=True, width=…)` — literal `**`
  disappears, tables get real borders, ANSI is emitted. This is the test
  strategy below, confirmed to work.
- `Live` re-renders **in place**, single-line via `\r\x1b[2K` and multi-line via
  `\x1b[1A` + `\x1b[2K`. Decision 1 is achievable.
- A left gutter around rendered markdown is a `Panel` with a custom `box` that
  draws only the left edge.
- One trap worth recording: `Live(auto_refresh=True)` drives redraws from a
  background thread, which never ticks inside a tight loop. The renderer must
  use `auto_refresh=False` and refresh explicitly on each delta. An earlier
  spike wrongly concluded `Live` could not redraw at all because of this.

### Constraint: replies taller than the viewport

A live region can only redraw what is still on screen. Once a reply grows past
the terminal height, earlier lines have scrolled and cannot be re-rendered.

The renderer therefore commits completed markdown blocks — a closed paragraph,
list or fenced block — as static output, and keeps only the trailing,
still-growing block live. That bounds the live region to roughly one block
regardless of reply length, and it is why the design streams *blocks* rather
than holding the whole answer live to the end.

`vertical_overflow` stays at rich's cropping default so a pathological single
block can never scroll the prompt away.

## The visual language

Glyphs, used consistently across trace, reply and selectors:

| Glyph | Meaning |
|---|---|
| `❯` | the user's input line |
| `●` | an event: a tool call |
| `⎿` | that event's result, indented beneath it |
| `┃` | the reply gutter, bounding the assistant's answer |
| `──` | a dim rule separating turns and titling a selector |

Palette stays the existing Vegapunk theme (`style.py`): Punk Records magenta for
reasoning, Egghead cyan for tools, Atlas red for failures, York yellow for
warnings, Shaka gold for the prompt. The redesign changes *layout*, not colours.

A rendered turn:

```
❯ list the files and count them

● list_dir(path=".")
  ⎿ 29 entries · requirements.txt, scheduler.log, … (+168 chars)

┃ Here's what's in the workspace root:
┃
┃   Directories (12)
┃     .agents/  .claude/  .git/  .venv/
┃
┃   Total: 29 entries — 12 dirs, 17 files

⠹ thinking… 4s · step 2 · 1.2k reasoning · 12.4k tok
```

The status line above is transient — it occupies one line while the model works
and erases itself when the reply begins. Nothing of the reasoning survives it.

## Surfaces

### Reply body

Rendered markdown inside the gutter: headings, lists, emphasis, links,
syntax-highlighted fenced code blocks, and real table borders. Re-rendered in
place as tokens arrive.

The gutter costs two columns and degrades cleanly when text wraps, which is why
it was chosen over a full box.

### Tool trace

One line per call, arguments rendered as `name(key="value")` rather than a repr
of a dict. The result goes on an indented `⎿` line, single-line, with multi-line
output collapsed to a summary plus a character count. Failures take Atlas red.

`_shorten`'s 200-character display cap is unchanged and keeps applying to both
renderers.

### Reasoning: shown as activity, never as text

Reasoning content is not displayed. On a real task it runs to thousands of
characters per step and buries the answer, which is the loudest reason the
current output reads as noisy.

What replaces it is a liveness signal, not a summary: while `ThinkingDelta`s
arrive, the status line stays up and its reasoning counter climbs. That
distinguishes *thinking* from *stalled* — the one thing the content was
actually being used for — without putting a word of it on screen.

`VEGAPUNK_SHOW_THINKING=1` restores the full text for debugging "why did it do
that?", which is the only case that wants it. Off by default.

Reasoning still stays in the conversation history regardless: the Anthropic API
rejects a later turn whose thinking blocks were altered, so it is stored and
replayed verbatim. Hidden is a display decision, not a data one.

**This is the one place `PlainRenderer`'s bytes change.** The `[reason]` line
disappears from the piped output too, because a display decision this deliberate
should not depend on whether you are on a terminal. Roughly four tests in
`tests/test_loop.py` pin that line and will be updated — the only expected churn
in the existing suite, and it is a behaviour change rather than drift.

### Spinner and live status

The spinner glyph stays. It gains a status tail: elapsed seconds, step number,
cumulative tokens, and — while reasoning streams — a character count of it. All
are already present on the events `loop.py` consumes (`TurnEnd.usage`, the
existing step counter, `ThinkingDelta`). It continues to draw only on a TTY and
to erase its own line on stop.

Because reasoning is no longer written out, the spinner now stays up *through*
the thinking phase rather than stopping at the first event, and stops when the
reply's first `TextDelta` arrives. That is the whole of the "model is thinking"
representation.

### Input prompt

`❯ ` replaces `you> `, styled through the existing `style.enabled` gate so
`NO_COLOR` still strips it. prompt_toolkit keeps owning the input line — it
provides history, completion, the pickers and multi-line composition, none of
which rich replaces.

### Startup header and errors

A compact header replacing the two plain print lines: model, workspace, and
backend readiness. A failed turn renders as a titled error block naming the
cause instead of `[error] <raw exception>`.

### Turn separators

A dim rule between turns so a long scrollback is scannable.

### Selectors

`vegapunk/menu.py` stays on prompt_toolkit — it is interactive, and rich is not.
It is restyled to match: a title rule, a full-width highlight bar for the
selected row, the `●` marker for the active value, and the existing hint line in
the shared dim style.

## Commands

The command set was reviewed as a whole rather than one at a time, which is what
surfaced these — each is a problem of the *set*, invisible when you look at any
single command.

### `/clear` is removed

It was an alias for `/new`. In every shell `clear` means "clear the screen", so
someone typing it to tidy their scrollback silently discarded their
conversation. Deleted outright rather than rebound: a command that clears the
screen is not something Vegapunk needs to own, and leaving the word bound to
anything keeps the trap warm. `/new` and `/reset` are unchanged.

### The plural commands are deprecated

`/model` vs `/models` and `/skill` vs `/skills` are one letter apart and do
different things. Once each singular has a picker, the plural has no job left —
a picker is already a list you choose from.

The test applied was whether the singular does the *complete* job. Both pass:

`/model` absorbs `/models` through the picker's drill-down:

| Input | Does |
|---|---|
| `/model` | pick a backend, then pick a model on it, then switch |
| `/model codex` | switch to codex on its default model |
| `/model codex gpt-5.4` | switch directly |

The middle step is the one `/models` existed for.

`/skill` absorbs `/skills` because its picker lists every skill with its
description and `Esc` stages nothing — so browsing is "open it and leave".

Both plurals stay registered and keep working, printing a dim one-line notice
naming the singular. They are removed in a later release, once the notice has
been seen a few times. Nothing breaks today; the deprecation is the warning, not
the removal.

**`/sessions` is exempt.** There is no `/session`, and neither `/load` nor
`/save` removes a saved conversation, so `/sessions remove` has no singular home.
It is a plural, but not half of a pair, so the rule does not reach it.

### One verb for removal: `remove`

`/sessions forget`, `/memory forget` and `/schedule remove` were three verbs for
one idea. `remove` wins:

| Was | Is |
|---|---|
| `/sessions forget <name>` | `/sessions remove <name>` |
| `/memory forget <id>` | `/memory remove <id>` |
| `/schedule remove <id>` | unchanged |

`forget` stays accepted as an alias on the two that had it, so existing habits
and anything scripted keep working.

### New: `/status`

One block answering "what am I actually running?": backend and whether its
credential is live, model, effort, context fullness, session name, scheduler
worker health, and workspace root. Every field already exists — spread between
the toolbar, which has room for two, and nowhere.

Every field is already computed somewhere, so `/status` assembles rather than
calculates — with one cost worth naming: credential liveness comes from
`provider_status()`, which reads credential stores including a Keychain
subprocess on macOS. That is the same round trip `/model`'s listing already
pays, and it is why `/status` is a command you ask for rather than something
on the toolbar.

### Considered and rejected: `/tools`

Listing the tool registry with its approval markers was proposed and dropped. It
duplicates the README table, and the information is not something you need
mid-conversation — the approval prompt names the tool at the moment it matters.

## Configuration

| Variable | Values | Default |
|---|---|---|
| `VEGAPUNK_UI` | `auto` \| `rich` \| `plain` | `auto` |
| `VEGAPUNK_SHOW_THINKING` | `0` \| `1` | `0` |

`auto` selects `RichRenderer` when the stream is a terminal, `PlainRenderer`
otherwise. `NO_COLOR` forces `plain`.

## Dependency

One addition: **`rich`**. Justified under `fundamentals.md` — it is the de-facto
terminal markdown and live-render library, and the alternative is hand-rolling a
markdown parser *and* a redraw engine, which is squarely the case that rule
exists for. It composes with prompt_toolkit (which keeps the input line) rather
than replacing it.

## Testing

- `PlainRenderer` keeps the current suite passing **unchanged**. Any test that
  starts failing indicates the plain path drifted, which is a defect.
- `RichRenderer` is driven against `rich.Console(file=StringIO,
  force_terminal=True, width=…)`, so assertions are deterministic and no real
  terminal is required.
- Pinned behaviours: markdown actually renders (no literal `**`); a multi-line
  tool result stays one trace line; the live region is closed before a trace
  line prints; a completed block is committed and only the trailing block stays
  live; reasoning content never reaches either stream while
  `VEGAPUNK_SHOW_THINKING` is off, and the status line reports it as activity; reasoning
  collapses and `/reason` reprints it; renderer selection follows
  `VEGAPUNK_UI`, TTY-ness and `NO_COLOR`.
- Selector tests keep driving the real widget through a prompt_toolkit pipe, as
  `tests/test_menu.py` does now.
- Command changes are pinned by their existing tests plus: `/clear` is no longer
  a command; `forget` and `remove` reach the same handler; `/models` and
  `/skills` still work and say they are deprecated; `/model`'s drill-down
  reaches the model step, which is what makes `/models` redundant; `/status`
  names every field it claims.

## Non-goals

**A full-screen multi-pane application.** Replies stream to stdout and the trace
to stderr; that split is what makes the REPL pipeable and what the live-status
and live-region decisions rest on. Owning the screen would mean re-implementing
token streaming inside a pane, losing `| less -R` and `2>trace.log`, and
rewriting `cli.py` and `loop.py`. It is a different project, not a larger
version of this one.

**Changing the palette.** Layout only; the Vegapunk theme is unchanged.

**Retroactive expansion of scrolled output.** See the reasoning section.

## Rollout

Four sequential PRs, each green:

1. `render.py` with both implementations and renderer selection; `loop.py` and
   `cli.py` call through it. No visible change yet — `PlainRenderer` is the
   default until the rich path lands.
2. The rich trace and reply: glyphs, gutter, live markdown with per-block
   commit, single-line tool results.
3. Hiding reasoning, and the live status tail on the spinner.
4. Chrome: startup header, error blocks, turn separators, input prompt, and the
   restyled selectors.
5. Commands: remove `/clear`, deprecate `/models` and `/skills`, unify removal
   on `remove`, and add `/status`. Last because it is independent of the
   renderer work and should not hold it up.
