# Renderer Seam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every byte Vegapunk prints during a turn behind a `Renderer`
interface, with one implementation that reproduces today's output exactly — so
the rich terminal UI can be built against a tested boundary.

**Architecture:** A new `vegapunk/render.py` defines a `Renderer` protocol and
`PlainRenderer`, which prints exactly what `loop.py` and `cli.py` print today.
`loop.trace` and `cli._render_reply` stop calling `print` and call the renderer
instead. `Session` owns the instance so the trace channel (stderr) and the reply
channel (stdout) share one object. **This PR is deliberately invisible:** the
existing suite is the proof, and every test must pass unchanged.

**Tech Stack:** Python 3.12, pytest, logpose (event stream), prompt_toolkit
(input only — untouched here). No new dependencies in this PR; `rich` arrives in
PR 2.

## Global Constraints

- Run everything through the repo venv: `.venv/bin/python -m pytest -q`.
- **No assertion may be weakened or dropped without equivalent coverage
  elsewhere.** The suite (643 tests) is the regression check for "output is
  byte-identical": a *display* test that needs changing means the plain path
  drifted — fix the code, not the test. Tests may move modules with the code
  they cover; that is the only permitted edit. (Ruled at pre-flight: Task 3
  deletes `loop._shorten`, which `tests/test_loop.py:562-573` imports.)
- Never commit on a red build; never delete or weaken a test to get green.
- Conventional Commits: `type(scope): summary`, imperative, ≤72 chars.
- Branch: `feat/render-seam`, cut from `master`. Never commit to `master`.
- Type-hint all public functions; no bare `except:`; no `except Exception: pass`.
- Reply text goes to **stdout**, trace to **stderr**. This split is load-bearing
  and must survive every task.
- Colour goes through `style.paint(text, code, stream)`, which returns the text
  unchanged when the stream should not get colour. Never emit raw ANSI.

---

### Task 1: The `Renderer` protocol and `PlainRenderer`

**Files:**
- Create: `vegapunk/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `vegapunk.style` (`paint`, `DIM`, `RED`, `CYAN`, `YELLOW`, `MAGENTA`,
  `BOLD`, `RESET`, `enabled`), `vegapunk.loop._shorten` is **not** used — the
  200-char cap moves into this module as `_shorten` so `render.py` has no
  dependency on `loop.py` (the dependency runs the other way in Task 3).
- Produces:
  ```python
  class Renderer(Protocol):
      def step(self, number: int) -> None: ...
      def reasoning(self, text: str) -> None: ...
      def reasoning_end(self) -> None: ...
      def tool_result(self, name: str, arguments: dict, content: str, is_error: bool) -> None: ...
      def note(self, text: str) -> None: ...
      def reply_delta(self, text: str) -> None: ...
      def reply_end(self) -> None: ...

  class PlainRenderer:  # implements Renderer
      def __init__(self) -> None: ...
  ```

- [ ] **Step 1: Write the failing tests**

Create `tests/test_render.py`:

```python
"""Tests for the renderer seam.

``PlainRenderer`` must print exactly what ``loop.py`` and ``cli.py`` printed
before the seam existed. These tests pin those bytes directly, so a drift shows
up here as well as in the display tests that consume it.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from vegapunk import style
from vegapunk.render import PlainRenderer


@pytest.fixture
def plain(monkeypatch):
    """A renderer with colour off, so assertions are on text not escapes."""
    monkeypatch.setattr("vegapunk.style.config", replace(style.config, color="never"))
    return PlainRenderer()


def test_step_prints_the_think_marker(plain, capsys):
    plain.step(2)

    assert capsys.readouterr().err == "  [think] step 2\n"


def test_tool_result_prints_name_arguments_and_result(plain, capsys):
    plain.tool_result("echo", {"text": "hi"}, "hi", is_error=False)

    assert capsys.readouterr().err == "  [tool] echo({'text': 'hi'}) -> hi\n"


def test_a_long_tool_result_is_truncated_with_a_count(plain, capsys):
    plain.tool_result("read", {}, "x" * 500, is_error=False)

    err = capsys.readouterr().err
    assert "… (+300 more chars)" in err
    assert "x" * 500 not in err


def test_reasoning_opens_one_line_and_closes_once(plain, capsys):
    plain.reasoning("think")
    plain.reasoning("ing")
    plain.reasoning_end()

    assert capsys.readouterr().err == "  [reason] thinking\n"


def test_reasoning_end_is_idempotent(plain, capsys):
    plain.reasoning("x")
    plain.reasoning_end()
    plain.reasoning_end()

    assert capsys.readouterr().err.count("\n") == 1


def test_reasoning_end_without_any_reasoning_prints_nothing(plain, capsys):
    plain.reasoning_end()

    assert capsys.readouterr().err == ""


def test_note_prints_on_the_watch_channel(plain, capsys):
    plain.note("the model ran out of tokens; this turn is cut off")

    assert capsys.readouterr().err == "  [note] the model ran out of tokens; this turn is cut off\n"


def test_reply_deltas_stream_to_stdout_with_one_prefix(plain, capsys):
    plain.reply_delta("all ")
    plain.reply_delta("done")
    plain.reply_end()

    captured = capsys.readouterr()
    assert captured.out == "vega> all done\n"
    assert captured.err == ""  # the reply never touches the watch channel


def test_an_empty_reply_still_gets_its_prompt_line(plain, capsys):
    plain.reply_end()

    assert capsys.readouterr().out == "vega> \n"


def test_a_reply_ending_in_a_newline_is_not_double_spaced(plain, capsys):
    plain.reply_delta("done\n")
    plain.reply_end()

    assert capsys.readouterr().out == "vega> done\n"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_render.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'vegapunk.render'`

- [ ] **Step 3: Write `vegapunk/render.py`**

```python
"""What a turn looks like — the seam every printed byte goes through.

``loop.py`` decides *what* happened; this decides how it appears. Splitting them
is what lets the terminal UI change without touching the event handling, and
what lets a piped or scripted session keep the plain text it has always had.

Two channels, deliberately separate. Reply text goes to stdout so it can be
piped; everything else — steps, reasoning, tool results, warnings — goes to
stderr, so watching the loop work never contaminates the answer.
"""

from __future__ import annotations

import sys
from typing import Protocol

from . import style


def _shorten(result: str, limit: int = 200) -> str:
    """Trim a tool result for display; the model always gets the full text.

    200 chars keeps a whole-file read from flooding the watch channel while
    still showing enough to recognise what came back. Slicing a ``str`` is
    codepoint-safe, so this never splits a character.
    """
    extra = len(result) - limit
    if extra <= 0:
        return result
    return f"{result[:limit]}… (+{extra:,} more char{'s' if extra != 1 else ''})"


class Renderer(Protocol):
    """How a turn is shown. One instance spans both channels of one turn."""

    def step(self, number: int) -> None:
        """A model round trip began."""

    def reasoning(self, text: str) -> None:
        """A fragment of the model's chain of thought arrived."""

    def reasoning_end(self) -> None:
        """No more reasoning this turn. Idempotent — callers needn't check."""

    def tool_result(self, name: str, arguments: dict, content: str, is_error: bool) -> None:
        """A tool ran and produced ``content``."""

    def note(self, text: str) -> None:
        """A warning about the loop itself, not about the model's answer."""

    def reply_delta(self, text: str) -> None:
        """A fragment of the assistant's answer arrived."""

    def reply_end(self) -> None:
        """The answer is complete. Idempotent."""


class PlainRenderer:
    """Unstyled, line-at-a-time output — what a pipe, a log, or a test sees.

    Every method writes complete lines and never moves the cursor, so the
    output survives redirection, ``less``, and CI unchanged.
    """

    def __init__(self) -> None:
        self._reasoning_open = False
        self._reasoning_reset = ""
        self._spoke = False
        self._line_open = False

    # -- the watch channel (stderr) -----------------------------------------

    def step(self, number: int) -> None:
        # The marker shows where each model round trip starts, which is what
        # makes batched-vs-chained tool calling visible.
        print(style.paint(f"  [think] step {number}", style.DIM, sys.stderr), file=sys.stderr)

    def reasoning(self, text: str) -> None:
        if not self._reasoning_open:
            # Colour opens once at the line start and resets once at the close,
            # not per fragment: a Ctrl-C landing mid-reasoning would otherwise
            # stain the whole terminal dim.
            self._reasoning_reset = style.RESET if style.enabled(sys.stderr) else ""
            opening = style.DIM + style.MAGENTA if self._reasoning_reset else ""
            print(f"{opening}  [reason] ", end="", file=sys.stderr, flush=True)
            self._reasoning_open = True
        print(text, end="", file=sys.stderr, flush=True)

    def reasoning_end(self) -> None:
        if self._reasoning_open:
            self._reasoning_open = False
            print(self._reasoning_reset, file=sys.stderr)

    def tool_result(self, name: str, arguments: dict, content: str, is_error: bool) -> None:
        marker = style.paint(
            f"  [tool] {name}", style.RED if is_error else style.CYAN, sys.stderr
        )
        print(f"{marker}({arguments}) -> {_shorten(content)}", file=sys.stderr)

    def note(self, text: str) -> None:
        print(style.paint(f"  [note] {text}", style.YELLOW, sys.stderr), file=sys.stderr)

    # -- the answer channel (stdout) ----------------------------------------

    def reply_delta(self, text: str) -> None:
        if not text:
            return
        if not self._spoke:
            print(self._prefix(), end="", flush=True)
            self._spoke = True
        print(text, end="", flush=True)
        self._line_open = not text.endswith("\n")

    def reply_end(self) -> None:
        if not self._spoke:
            print(self._prefix())  # an empty reply still gets its prompt line
        elif self._line_open:
            print()
        self._spoke = False
        self._line_open = False

    @staticmethod
    def _prefix() -> str:
        """Punk Records speaking. The reset lands before the space so the reply
        itself streams in the default colour."""
        return style.paint("vega>", style.BOLD + style.MAGENTA, sys.stdout) + " "
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_render.py -q`
Expected: PASS (10 tests)

- [ ] **Step 5: Run the whole suite — nothing else may have moved**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, 653 tests (643 existing + 10 new)

- [ ] **Step 6: Commit**

```bash
git add vegapunk/render.py tests/test_render.py
git commit -m "feat(render): add the Renderer seam and a plain implementation"
```

---

### Task 2: Renderer selection

**Files:**
- Modify: `vegapunk/render.py` (append `pick`)
- Modify: `vegapunk/config.py` (add `ui`)
- Test: `tests/test_render.py` (append)

**Interfaces:**
- Consumes: `PlainRenderer` from Task 1; `vegapunk.config.Config`.
- Produces: `render.pick(cfg: Config = config) -> Renderer`, and
  `Config.ui: str` read from `VEGAPUNK_UI`, one of `auto` | `rich` | `plain`.
  In this PR `pick` always returns `PlainRenderer`; `RichRenderer` is added in
  PR 2 and `pick` is the only place that will change.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_render.py`:

```python
# ---------------------------------------------------------------------------
# choosing a renderer
# ---------------------------------------------------------------------------


def test_pick_returns_a_renderer_satisfying_the_protocol():
    from vegapunk.render import Renderer, pick

    chosen = pick()

    for method in ("step", "reasoning", "reasoning_end", "tool_result", "note",
                   "reply_delta", "reply_end"):
        assert callable(getattr(chosen, method)), f"{method} missing"
    assert isinstance(chosen, Renderer)  # runtime_checkable


def test_an_unknown_ui_mode_is_refused_by_name():
    """A typo in VEGAPUNK_UI should say so at launch rather than silently
    picking a default the user did not ask for."""
    from vegapunk.render import pick

    with pytest.raises(ValueError, match="VEGAPUNK_UI"):
        pick(replace(style.config, ui="fancy"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_render.py -q -k pick`
Expected: both FAIL. The first with `ImportError: cannot import name 'pick'`;
the second with `TypeError: __init__() got an unexpected keyword argument 'ui'`,
because `Config.ui` does not exist yet either. Both are fixed by Steps 3–4.

- [ ] **Step 3: Add `ui` to `vegapunk/config.py`**

Insert after the `color` field (around `config.py:57`):

```python
    # Which renderer draws a turn: "auto" (rich when the stream is a terminal,
    # plain otherwise), "rich" (always), or "plain" (always). NO_COLOR forces
    # plain, matching how `color` above already behaves.
    ui: str = os.getenv("VEGAPUNK_UI", "auto")
```

- [ ] **Step 4: Append `pick` to `vegapunk/render.py`**

```python
UI_MODES = ("auto", "rich", "plain")


def pick(cfg: Config = config) -> Renderer:
    """The renderer for this process.

    ``auto`` follows the terminal, which is the same rule ``style.enabled``
    already applies to colour, so one mental model covers both. ``NO_COLOR``
    forces plain: a user asking for no escape codes is not asking for a live
    re-rendering region either.

    Raises:
        ValueError: If ``VEGAPUNK_UI`` is not a known mode. Said out loud rather
            than defaulting, because silently ignoring the setting is how you
            spend an afternoon wondering why it does nothing.
    """
    if cfg.ui not in UI_MODES:
        raise ValueError(
            f"Unknown VEGAPUNK_UI {cfg.ui!r} — expected one of: {', '.join(UI_MODES)}."
        )
    return PlainRenderer()
```

Add to the imports at the top of `render.py`:

```python
from .config import Config, config
```

And decorate the protocol so `isinstance` works:

```python
from typing import Protocol, runtime_checkable


@runtime_checkable
class Renderer(Protocol):
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_render.py -q`
Expected: PASS (12 tests)

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, 655 tests

- [ ] **Step 7: Commit**

```bash
git add vegapunk/render.py vegapunk/config.py tests/test_render.py
git commit -m "feat(render): choose a renderer from VEGAPUNK_UI and the terminal"
```

---

### Task 3: `loop.trace` prints through the renderer

**Files:**
- Modify: `vegapunk/loop.py` (imports, `run`, `trace`; delete `_ReasoningLine`
  and `_shorten`)
- Modify: `vegapunk/session.py:83-88` (pass the renderer into `trace`)
- Modify: `vegapunk/session.py:34-56` (accept and hold a renderer)

**Interfaces:**
- Consumes: `render.Renderer`, `render.pick` from Tasks 1–2.
- Produces:
  - `loop.trace(events, renderer) -> Generator[TextDelta, None, tuple[str, int | None]]`
    — `renderer` is now a required second positional parameter.
  - `loop.run(agent, user_input, renderer=None) -> str` — defaults to
    `render.pick()` so script callers keep the one-argument-plus-agent shape.
  - `Session(backend, tools, system_prompt=…, max_steps=…, approver=None,
    renderer=None)` and the read-only property `Session.renderer -> Renderer`,
    which `cli` uses in Task 4 so both channels share one instance.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_loop.py`:

```python
def test_trace_prints_through_the_renderer_it_is_given():
    """The seam is real: nothing reaches a stream except through the renderer.

    Driven with a recording double rather than capsys, so a regression that
    re-adds a bare `print` to loop.py fails here instead of passing silently
    because the bytes happened to match.
    """
    class Recorder:
        def __init__(self):
            self.calls = []

        def __getattr__(self, name):
            def record(*args, **kwargs):
                self.calls.append((name, args, kwargs))
            return record

    recorder = Recorder()
    agent, _provider = agent_for(
        [wants(call("echo", {"text": "hi"}), thinking="mulling"), says("done")], tools=TOOLS
    )
    generator = trace(stream_sync(agent, "go", conversation=Conversation()), recorder)
    while True:
        try:
            next(generator)
        except StopIteration:
            break

    named = [name for name, _args, _kwargs in recorder.calls]
    assert named.count("step") == 2
    assert "reasoning" in named
    assert "reasoning_end" in named
    assert "tool_result" in named
    tool = next(c for c in recorder.calls if c[0] == "tool_result")
    assert tool[1][0] == "echo"           # name
    assert tool[1][1] == {"text": "hi"}   # arguments
    assert tool[1][2] == "hi"             # content
    assert tool[1][3] is False            # is_error
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_loop.py -q -k renderer`
Expected: FAIL — `TypeError: trace() takes 1 positional argument but 2 were given`

- [ ] **Step 3: Rewrite the printing sites in `vegapunk/loop.py`**

Replace the import block:

```python
from logpose import (
    Agent,
    Conversation,
    Event,
    MaxIterationsError,
    RunEnd,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolResult,
    TurnEnd,
    stream_sync,
)

from . import render
from .render import Renderer
```

Delete the `from . import style` import, the `_ReasoningLine` class, and the
`_shorten` function — `render.py` owns both now.

Then delete `test_shorten_leaves_short_results_alone` and
`test_shorten_reports_how_much_it_hid` from `tests/test_loop.py` (lines
562-573). They cover a helper that has moved; `tests/test_render.py`'s
`test_a_long_tool_result_is_truncated_with_a_count` from Task 1 already covers
the behaviour through its new home. This is the *only* permitted test edit.

Change `run`:

```python
def run(agent: Agent, user_input: str, renderer: Renderer | None = None) -> str:
    """One-shot: run a single request to completion and return the reply.

    Drains the turn stream internally (no live rendering by the caller), so
    script callers keep the simple call-and-get-a-string contract.
    """
    turns = trace(
        stream_sync(agent, user_input, conversation=Conversation()),
        renderer or render.pick(),
    )
    while True:
        try:
            next(turns)
        except StopIteration as stop:
            reply, _context_tokens = stop.value  # one-shots don't track fullness
            return reply
```

Change `trace`'s signature and the five printing sites:

```python
def trace(
    events: Generator[Event, None, None], renderer: Renderer
) -> Generator[TextDelta, None, tuple[str, int | None]]:
```

| Was | Becomes |
|---|---|
| `print(style.paint(f"  [think] step {step}", …))` | `renderer.step(step)` |
| `reasoning.write(event.text)` | `renderer.reasoning(event.text)` |
| `reasoning.close()` (3 sites) | `renderer.reasoning_end()` |
| the `[tool]` `print(...)` block | `renderer.tool_result(event.name, pending_args.pop(event.id, {}), event.content, event.is_error)` |
| the `[note]` `print(...)` block | `renderer.note("the model ran out of tokens; this turn is cut off")` |

The `finally:` block becomes:

```python
    finally:
        spinner.stop()
        renderer.reasoning_end()
        events.close()
```

`_Spinner` stays in `loop.py` for this PR; it moves to the renderer in PR 3.

- [ ] **Step 4: Thread the renderer through `vegapunk/session.py`**

Add to the imports:

```python
from . import render, style
from .render import Renderer
```

Add the parameter and the field in `__init__`:

```python
        approver: Approver | None = None,
        renderer: Renderer | None = None,
    ) -> None:
        ...
        # One renderer for both channels of a turn: the trace writes through it
        # and so does the reply, so they can coordinate when one of them needs
        # to own the cursor.
        self._renderer = renderer or render.pick()
```

Add the property beside `model_label`:

```python
    @property
    def renderer(self) -> Renderer:
        """The renderer this session's turns print through."""
        return self._renderer
```

And pass it in `send`:

```python
            reply, context_tokens = yield from trace(
                stream_sync(self._agent, user_input, conversation=self._conversation),
                self._renderer,
            )
```

- [ ] **Step 5: Run the new test**

Run: `.venv/bin/python -m pytest tests/test_loop.py -q -k renderer`
Expected: PASS

- [ ] **Step 6: Run the whole suite — the output must be byte-identical**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, 654 tests (656 minus the 2 moved `_shorten` tests). **Every
pre-existing display test passes unmodified.** If any display test fails, `PlainRenderer` differs from what `loop.py` used to print —
fix `render.py`, not the test.

- [ ] **Step 7: Commit**

```bash
git add vegapunk/loop.py vegapunk/session.py tests/test_loop.py
git commit -m "refactor(loop): print through the renderer instead of stdout"
```

---

### Task 4: `cli` renders the reply through the same instance

**Files:**
- Modify: `vegapunk/cli.py:70-96` (`_render_reply`), `cli.py:35-38` (delete
  `_vega_prefix`)
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `Session.renderer` from Task 3.
- Produces: `cli._render_reply(events, renderer) -> None` — takes the renderer
  explicitly so the function stays testable without building a Session.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_the_reply_is_rendered_through_the_sessions_renderer(capsys):
    """One renderer spans both channels of a turn.

    If the CLI printed the reply itself, the trace and the reply would be two
    independent writers to one terminal — which is exactly what breaks once the
    reply owns a live region.
    """
    from vegapunk.cli import _render_reply

    class Recorder:
        def __init__(self):
            self.deltas = []
            self.ended = 0

        def reply_delta(self, text):
            self.deltas.append(text)

        def reply_end(self):
            self.ended += 1

    recorder = Recorder()

    def events():
        yield TextDelta("all ")
        yield TextDelta("done")
        return "all done"

    _render_reply(events(), recorder)

    assert recorder.deltas == ["all ", "done"]
    assert recorder.ended == 1
    assert capsys.readouterr().out == ""  # the CLI printed nothing itself
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q -k sessions_renderer`
Expected: FAIL — `TypeError: _render_reply() takes 1 positional argument but 2 were given`

- [ ] **Step 3: Rewrite `_render_reply` in `vegapunk/cli.py`**

```python
def _render_reply(events: Generator[TextDelta, None, str], renderer: Renderer) -> None:
    """Stream a turn's reply through ``renderer``.

    Kept out of ``main``'s loop, which has enough going on. The pull-by-``next``
    shape is deliberate (rather than a ``for``) so a Ctrl-C landing between
    pulls still surfaces from here for ``main`` to catch and roll back.
    """
    while True:
        try:
            event = next(events)
        except StopIteration:  # .value carries the reply; already rendered
            break
        if isinstance(event, TextDelta):
            renderer.reply_delta(event.text)
    renderer.reply_end()
```

Delete `_vega_prefix` — `PlainRenderer._prefix` owns it now. Add
`from .render import Renderer` to the imports.

Update the call site in `main`:

```python
                events = session.send(user_input)
                _render_reply(events, session.renderer)
```

- [ ] **Step 4: Run the new test**

Run: `.venv/bin/python -m pytest tests/test_cli.py -q -k sessions_renderer`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, 655 tests.

- [ ] **Step 6: Verify against the real app — the point of the PR is that nothing changed**

```bash
.venv/bin/python -m vegapunk
```

Ask `what time is it`. Confirm by eye: `[think] step 1`, a `[reason]` line, a
cyan `[tool] get_time(...)`, the reply streaming token by token behind `vega>`.
Then Ctrl-C mid-reply and confirm the prompt returns cleanly. Then:

```bash
echo "what time is it" | .venv/bin/python -m vegapunk 2>trace.log
```

Confirm stdout holds only the reply and `trace.log` only the trace — the split
must have survived.

- [ ] **Step 7: Commit**

```bash
git add vegapunk/cli.py tests/test_cli.py
git commit -m "refactor(cli): render the reply through the session's renderer"
```

---

### Task 5: Document the seam and open the PR

**Files:**
- Modify: `README.md` (env-var table, project layout)

**Interfaces:**
- Consumes: everything above. Produces nothing new.

- [ ] **Step 1: Add `VEGAPUNK_UI` to the README env-var table**

Insert after the `VEGAPUNK_COLOR` row:

```markdown
| `VEGAPUNK_UI` | How a turn is drawn: `auto` (follow the terminal), `rich`, or `plain`. `NO_COLOR` forces `plain` | `auto` |
```

- [ ] **Step 2: Add `render.py` to the project layout block**

Insert after the `loop.py` line:

```
  render.py      # how a turn is shown: every printed byte goes through here
```

- [ ] **Step 3: Run the whole suite one last time**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS, 655 tests.

- [ ] **Step 4: Commit and open the PR**

```bash
git add README.md
git commit -m "docs: describe the renderer seam and VEGAPUNK_UI"
git push -u origin feat/render-seam
gh pr create --base master --head feat/render-seam \
  --title "refactor: route every printed byte through a Renderer seam" \
  --body "$(cat <<'BODY'
Groundwork for the TUI redesign
(`docs/superpowers/specs/2026-08-06-tui-redesign-design.md`).

Every byte a turn prints now goes through a `Renderer`. `PlainRenderer`
reproduces today's output exactly, so **this PR is deliberately invisible** —
which is the point: the rich terminal UI in the next PR lands against a tested
boundary rather than against `print` calls scattered through `loop.py` and
`cli.py`.

- `vegapunk/render.py` — the `Renderer` protocol, `PlainRenderer`, and `pick()`.
- `loop.trace` takes a renderer and calls it; `_ReasoningLine` and `_shorten`
  move into `render.py`.
- `Session` owns one instance so the trace (stderr) and the reply (stdout)
  share it — needed in PR 2, when the reply starts owning a live region and the
  two channels must coordinate.
- `VEGAPUNK_UI` (`auto`|`rich`|`plain`) selects it; an unknown value is refused
  by name rather than silently ignored.

## Verification

The whole existing suite passes **unmodified** — that is the regression check
for "byte-identical output", and any test needing a change would have meant the
plain path drifted. Plus new tests pinning `PlainRenderer`'s bytes directly, and
one driving `loop.trace` with a recording double so a re-added bare `print`
fails loudly instead of passing because the bytes happened to match.

Also run by hand: a real turn with a tool call and a Ctrl-C mid-reply, and
`... | vegapunk 2>trace.log` to confirm the stdout/stderr split survived.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```

---

## Self-Review

**Spec coverage.** This plan covers the spec's "renderer seam", "where rendering
is called from", and the `VEGAPUNK_UI` half of Configuration — i.e. PR 1 of the
five-PR rollout. Everything else in the spec (the rich renderer, hidden
reasoning and live status, chrome and selectors, commands) is explicitly out of
scope here and gets its own plan once this lands, written against the real
interface rather than a predicted one.

**Deliberately deferred, and why.** `_Spinner` stays in `loop.py` rather than
moving to the renderer in this PR: it is the one piece whose behaviour differs
between the plain and rich paths (PR 3 gives it a live status tail), so moving
it now would mean designing that interface before the rich renderer exists.

**Type consistency.** `Renderer`'s seven methods are spelled identically in the
protocol (Task 1), the recording doubles (Tasks 3–4), and the call sites
(Task 3's mapping table). `pick` returns `Renderer` in Task 2 and is consumed as
that type in Tasks 3–4. `Session.renderer` is defined in Task 3 and consumed in
Task 4.

**Known risk.** Task 3 touches `Session.send`'s `try` block, which contains the
Ctrl-C rollback — the most delicate code in the repo (three bugs there in one
week). The change is confined to the `trace(...)` call's arguments and does not
touch the `except BaseException` handler or the checkpoint. Task 4's manual
verification includes a real Ctrl-C mid-reply for exactly this reason; the fake
provider has no event loop and cannot reach that failure mode.
