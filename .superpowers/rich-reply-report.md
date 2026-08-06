# RichRenderer — implementation report

## What was built

1. **`requirements.txt`**: added `rich>=13.0` with a comment explaining why
   (terminal markdown + in-place redraw; de-facto library, not worth hand
   rolling). Installed into `.venv` (`rich==15.0.0` resolved).

2. **`vegapunk/render.py`**: `RichRenderer`, a full `Renderer` implementation.
   - **Trace methods** (`step`, `reasoning`, `reasoning_end`, `tool_result`,
     `note`) delegate verbatim to an internal `PlainRenderer` — same bytes,
     same stream (stderr), unchanged.
   - **Reply body** (`reply_delta`/`reply_end`/`reply_abort`): accumulates
     text in a buffer and re-renders it as `rich.markdown.Markdown` inside a
     `rich.panel.Panel`, redrawn in place via `rich.live.Live`
     (`auto_refresh=False`, explicit `refresh()` per delta — see trap #1
     below; `vertical_overflow="crop"` — trap #2). Writes to stdout via an
     injectable `rich.console.Console` (defaults to `Console(file=sys.stdout)`
     — tests inject a `Console(file=io.StringIO(), force_terminal=True,
     width=60)` for determinism).
   - **Gutter**: a custom `rich.box.Box` (`_GUTTER_BOX`) whose top/bottom
     border rows are all spaces (blank) and whose content rows show a bar
     (`│`) on the left and a blank padding column on the right — so `Panel`
     draws only a left-edge accent bar, not a full box.
   - **`tool_call`**: stops/finalises the current live region and resets the
     buffer, so a later `reply_delta` opens a fresh one.
   - **`reply_abort`**: must emit nothing. `Live.stop()` always performs one
     last refresh plus a cursor-show with no public flag to suppress it, so
     the implementation points the console at a scratch `io.StringIO()` for
     the duration of the `stop()` call (then restores the real file) —
     runs `Live`'s actual public teardown, which keeps `Console`'s internal
     live-slot/render-hook accounting balanced for the next turn, without any
     bytes reaching the real stream. Verified empirically before writing it
     into the class (see "design decisions" below).

3. **`render.pick()`**: now returns `RichRenderer` when `cfg.ui == "rich"`
   (always, even off a real terminal — an explicit request wins), or when
   `cfg.ui == "auto"` and `sys.stdout.isatty() and style.enabled(sys.stdout)`
   — see the design-decision note below on why `isatty()` had to be explicit
   rather than relying on `style.enabled` alone. `Config.ui`'s comment and
   `pick`'s docstring updated; both previously said "always returns plain."

## Test command and output

```
.venv/bin/python -m pytest -q
```
```
672 passed in 4.44s
```
(Baseline was 665. Added 7 new `RichRenderer` tests in `tests/test_render.py`
and split one existing `pick()` parametrized test into two, net +7.)

Also ran narrowly first while iterating: `.venv/bin/python -m pytest -q
tests/test_render.py` → `28 passed`.

New/changed tests in `tests/test_render.py`:
- `test_pick_returns_plain_when_stdout_is_not_a_terminal` (was
  `test_pick_returns_plain_for_every_known_mode_today`, split — see below)
- `test_pick_returns_rich_for_explicit_rich_mode_even_off_a_terminal`
- `test_rich_reply_renders_markdown_instead_of_literal_syntax`
- `test_rich_reply_is_bounded_by_a_left_gutter_bar`
- `test_rich_tool_call_closes_the_live_region_so_the_next_reply_starts_fresh`
- `test_rich_reply_abort_emits_nothing`
- `test_rich_reply_abort_with_no_reply_in_progress_emits_nothing`
- `test_rich_reply_abort_then_a_new_reply_still_renders_cleanly`
- `test_rich_trace_methods_still_produce_the_plain_bytes_on_stderr`

All RichRenderer tests use `rich.console.Console(file=io.StringIO(),
force_terminal=True, width=60)` injected into the renderer — no real
terminal, deterministic. `tool_call`'s and `reply_abort`'s effects are
asserted through the public interface (subsequent `reply_delta`/`reply_end`
output), not by reaching into `_live`/`_buffer`, per the testing rule about
public contracts over implementation details.

## Design decisions worth knowing about

1. **`pick()`'s "auto" gate needed an explicit `isatty()` check, not just
   `style.enabled()`.** The task said to reuse `style.enabled` for the
   terminal/`NO_COLOR` check. Doing that literally
   (`cfg.ui == "auto" and style.enabled(sys.stdout)`) broke an existing test:
   `tests/test_cli.py::test_vega_prefix_is_bold_magenta_when_forced`, which
   forces `VEGAPUNK_COLOR=always` under capsys (non-TTY) to assert the exact
   plain `vega>` ANSI prefix bytes. `style.enabled` returns `True` for
   `color == "always"` unconditionally, regardless of `isatty()` — that's
   correct for "let ANSI codes survive a pipe into `less -R`," but wrong as a
   proxy for "a live, cursor-addressed redraw makes sense here," since `Live`
   fundamentally needs a real terminal. Fixed by requiring
   `sys.stdout.isatty() and style.enabled(sys.stdout)` for "auto" — `rich`
   mode is still forced unconditionally (explicit request beats detection,
   same precedent as `VEGAPUNK_COLOR=always`). This is a deliberate, narrow
   change from the letter of the task's suggested expression, made because
   the task's own governing rule ("if any existing test breaks, your pick
   logic is wrong, not the test") pointed straight at it. I did not weaken or
   delete the existing test — it now passes unchanged.

2. **Split `test_pick_returns_plain_for_every_known_mode_today` into two
   tests** rather than editing it in place: its own docstring predicted this
   exact moment ("this test breaks the day a rich renderer lands and
   'auto'/'rich' start returning something else"). One test now covers
   `auto`/`plain` (still `PlainRenderer` under non-TTY pytest), the other
   covers `rich` (now `RichRenderer`, forced). No assertion was weakened —
   the old test's single assertion is preserved for the two modes where it
   still holds, and a new, equally strict assertion replaces it for `rich`.

3. **`reply_abort`'s "emit nothing" guarantee** required looking at
   `Live.stop()`'s source (no `--no-verify`-style shortcut here — verified
   empirically in a scratch script that the swap-console-file approach
   produces byte-identical output before/after `reply_abort()`, and that a
   subsequent reply still renders correctly afterward, before writing it into
   the class). Reaching into `Live`/`Console` internals was avoided — the
   scratch-buffer swap uses only `Console.file` (a public, documented,
   settable property) and `Live.stop()` (public), no underscored attributes.

4. **No "vega>"-style prefix or title on the Rich panel.** Scope was "the
   reply body only," and the task's pinned test list didn't ask for one, so
   none was added — keeping the change minimal. Worth a follow-up decision if
   the visual design wants a `vega` label on the panel later (e.g. via
   `Panel(title=...)`, which the gutter box supports since its top row's
   fill char is a space).

5. **Empty replies**: `PlainRenderer.reply_end()` prints a bare `vega> `
   prompt line even for an empty reply. `RichRenderer.reply_end()` on an
   empty reply (no prior `reply_delta`) renders nothing at all — there was no
   live region to finalise. Not covered by the pinned test list; flagging in
   case the empty-reply case should visually announce itself later.

## What could not be done / left out

- Nothing in-scope was skipped. `loop.py` and `cli.py` were not touched, per
  the task's explicit instruction — `pick()` is the only wiring point, and
  its callers (`session.py`) already call it without a renderer arg.
- No lint/type-check tool (ruff/mypy) is configured in this repo (no
  `pyproject.toml`, no `ruff`/`mypy` in `.venv/bin`), so the only gate run
  was `pytest`, per the repo's own testing rule ("Match whatever the repo
  already uses").
