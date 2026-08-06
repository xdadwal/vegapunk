# reply_break fix — report

## What changed

`loop.py`'s `trace()` used to close a spoken line before a same-turn tool call
by yielding a synthetic `TextDelta("\n")`, indistinguishable from real model
speech once it reached `cli._render_reply`. For `PlainRenderer` that happened
to produce the right bytes; for `RichRenderer` it corrupted the markdown
buffer that gets re-rendered live (a layout effect achieved by injecting fake
content into the model's prose).

- `vegapunk/render.py`
  - `Renderer` protocol: added `reply_break()`, documented as existing
    because layout is the renderer's business, not the loop's — same shape as
    `tool_call` existing separately from `tool_result`.
  - `PlainRenderer.reply_break()`: prints an unconditional newline and clears
    `_line_open`, leaving `_spoke` untouched — reproducing exactly what
    `reply_delta("\n")` used to do, so a later `reply_delta` this turn still
    skips the `vega> ` prefix and `reply_end` doesn't add a stray blank line.
  - `RichRenderer.reply_break()`: a no-op. `loop.py` always calls it
    immediately before `tool_call`, and `tool_call` already finalises the
    live region (`_finish_live()`) on its way to handing the screen back —
    finalising twice would be redundant, not just harmless.
- `vegapunk/loop.py`: `trace()` now calls `renderer.reply_break()` instead of
  yielding `TextDelta("\n")` when the model spoke before a tool call.
- `tests/test_loop.py`:
  - `_drive()` takes an optional `renderer=` so a test can inject a spy.
  - `test_speaking_before_a_tool_call_closes_the_spoken_line` updated (not
    deleted/weakened): asserts yielded deltas are now
    `["Let me check.", "done"]` (no synthetic `"\n"`), and — via
    `mock.Mock(wraps=PlainRenderer())` — that `reply_break` was actually
    called, before `tool_call`.
- `tests/test_render.py`: added
  - `test_reply_break_closes_the_spoken_line_like_the_synthetic_newline_it_replaces`
    — pins the exact stdout bytes for the speak-then-tool case.
  - `test_reply_break_leaves_spoke_set_so_a_later_delta_skips_the_prefix`
  - `test_reply_break_then_reply_end_does_not_add_a_stray_blank_line`
  - `test_rich_reply_break_before_tool_call_matches_tool_call_alone` — proves
    `reply_break` draws nothing for `RichRenderer` by diffing against a run
    that never calls it.
  - Added `"reply_break"` to the protocol method-presence check in
    `test_pick_returns_a_renderer_satisfying_the_protocol`.

## Test command and output

```
.venv/bin/python -m pytest -q
```

```
680 passed in 4.68s
```

Baseline was 676; the 4 new tests account for the difference. No existing
assertion was weakened — the one pinned test that encoded the bug
(`test_speaking_before_a_tool_call_closes_the_spoken_line`) was updated to
assert the corrected contract, not deleted.

## Byte-identity proof (speak-then-tool case, PlainRenderer)

Ran directly against `vegapunk.render.PlainRenderer`, replaying the exact
sequence of calls the old code path and the new code path each produce:

```python
# OLD: loop.py yielded TextDelta("\n"), which cli._render_reply passed to reply_delta
old.reply_delta("Let me check.")
old.reply_delta("\n")
old.reply_delta("done")
old.reply_end()
# -> 'vega> Let me check.\ndone\n'

# NEW: loop.py calls renderer.reply_break() directly
new.reply_delta("Let me check.")
new.reply_break()
new.reply_delta("done")
new.reply_end()
# -> 'vega> Let me check.\ndone\n'

old_out == new_out  # True
```

Both produce `'vega> Let me check.\ndone\n'` — byte-identical. This is also
pinned as a regression test in
`test_reply_break_closes_the_spoken_line_like_the_synthetic_newline_it_replaces`.

## Anything not done

Nothing skipped. `ruff`/`mypy` are not installed in `.venv` in this
environment, so lint/type-check could not be run locally; the diff is small,
type-hinted (`-> None`), and follows the surrounding style, but that check is
unverified here.
