"""Tests for the renderer seam.

``PlainRenderer`` must print exactly what ``loop.py`` and ``cli.py`` printed
before the seam existed. These tests pin those bytes directly, so a drift shows
up here as well as in the display tests that consume it.
"""

from __future__ import annotations

import io
from dataclasses import replace

import pytest
from rich.console import Console

from vegapunk import style
from vegapunk.render import UI_MODES, PlainRenderer, RichRenderer, _split_completed_markdown, pick


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


def test_shorten_does_not_touch_a_result_at_or_under_the_limit():
    from vegapunk.render import _shorten

    assert _shorten("y" * 200) == "y" * 200
    assert _shorten("short") == "short"


def test_shorten_uses_the_singular_when_exactly_one_char_over():
    from vegapunk.render import _shorten

    assert _shorten("y" * 201) == "y" * 200 + "… (+1 more char)"


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


def test_reply_break_closes_the_spoken_line_like_the_synthetic_newline_it_replaces(
    plain, capsys
):
    """loop.py used to fake this with ``reply_delta("\\n")``; reply_break()
    replaces that call site. Byte-for-byte, the two must agree — this pins the
    exact stdout a speak-then-tool turn produces so a future change to either
    path shows up here."""
    plain.reply_delta("Let me check.")
    plain.reply_break()
    plain.reply_delta("done")
    plain.reply_end()

    assert capsys.readouterr().out == "vega> Let me check.\ndone\n"


def test_reply_break_leaves_spoke_set_so_a_later_delta_skips_the_prefix(plain, capsys):
    plain.reply_delta("first")
    plain.reply_break()
    plain.reply_delta(" second")

    assert capsys.readouterr().out == "vega> first\n second"


def test_reply_break_then_reply_end_does_not_add_a_stray_blank_line(plain, capsys):
    plain.reply_delta("done")
    plain.reply_break()
    plain.reply_end()

    assert capsys.readouterr().out == "vega> done\n"  # not "vega> done\n\n"


def test_reply_end_called_twice_prints_a_second_prompt_line(plain, capsys):
    """Pins the actual (non-idempotent) behaviour: reply_end prints a prompt
    line every time it's called, so a caller that calls it twice for one turn
    gets two 'vega> ' lines. Callers must call it exactly once per turn that
    reaches it — reply_abort() is the tool for the paths that don't."""
    plain.reply_delta("done")
    plain.reply_end()
    plain.reply_end()

    assert capsys.readouterr().out == "vega> done\nvega> \n"


def test_reply_abort_resets_state_without_printing_so_the_next_turn_gets_its_prefix(
    plain, capsys
):
    """Regression for the renderer becoming per-session state: a turn that ends
    abnormally (Ctrl-C, a failed turn) must not leak _spoke/_line_open into the
    next turn's first delta, and reply_abort() must not print anything itself —
    the caller's own interrupt/error message follows immediately after."""
    plain.reply_delta("par")  # first turn dies mid-reply, e.g. Ctrl-C
    plain.reply_abort()
    assert capsys.readouterr().out == "vega> par"  # abort itself printed nothing more

    plain.reply_delta("second turn reply")
    plain.reply_end()
    assert capsys.readouterr().out == "vega> second turn reply\n"


def test_reply_abort_after_an_empty_aborted_turn_still_prefixes_the_next_reply(plain, capsys):
    """The trickier case the reviewer called out: if reply_end ran instead of
    reply_abort on an aborted turn with no output at all, it would take the
    'elif self._line_open' branch and print a bare newline with no prompt at
    all. reply_abort must leave the invariant intact for the next turn."""
    plain.reply_abort()  # no reply_delta calls at all — nothing spoken
    assert capsys.readouterr().out == ""

    plain.reply_delta("next")
    plain.reply_end()
    assert capsys.readouterr().out == "vega> next\n"


# ---------------------------------------------------------------------------
# choosing a renderer
# ---------------------------------------------------------------------------


def test_pick_returns_a_renderer_satisfying_the_protocol():
    from vegapunk.render import Renderer, pick

    chosen = pick()

    for method in ("step", "reasoning", "reasoning_end", "tool_result", "note",
                   "reply_delta", "reply_break", "reply_end"):
        assert callable(getattr(chosen, method)), f"{method} missing"
    assert isinstance(chosen, Renderer)  # runtime_checkable


@pytest.mark.parametrize("mode", ["auto", "plain"])
def test_pick_returns_plain_when_stdout_is_not_a_terminal(mode):
    """Under pytest, sys.stdout is never a real terminal (capture redirects
    it), so 'auto' — which follows the terminal — lands on plain right beside
    'plain' itself. This is the guarantee the task cared about: a rich
    renderer existing must not change what the existing (non-TTY) test suite
    sees."""
    from vegapunk.render import pick

    assert isinstance(pick(replace(style.config, ui=mode)), PlainRenderer)


def test_pick_returns_rich_for_explicit_rich_mode_even_off_a_terminal():
    """'rich' is an explicit request, not a terminal-detection outcome — it
    wins the same way VEGAPUNK_COLOR=always beats a non-TTY stream for
    style.enabled. Forcing it is the whole point of the mode existing."""
    from vegapunk.render import RichRenderer, pick

    assert isinstance(pick(replace(style.config, ui="rich")), RichRenderer)


def test_an_unknown_ui_mode_is_refused_by_name():
    """A typo in VEGAPUNK_UI should say so at launch rather than silently
    picking a default the user did not ask for."""
    from vegapunk.render import pick

    with pytest.raises(ValueError, match="VEGAPUNK_UI"):
        pick(replace(style.config, ui="fancy"))


def test_the_plain_renderer_prints_nothing_when_a_tool_is_requested(plain, capsys):
    """The hook exists for renderers that own screen space. Plain prints the
    arguments beside the result, so it has nothing to say before the tool runs
    — and must not start a line it would then have to unpick."""
    plain.tool_call("echo", {"text": "hi"})

    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


# ---------------------------------------------------------------------------
# RichRenderer — the reply body only; trace behaviour is PlainRenderer's,
# delegated verbatim. No real terminal: a Console over an io.StringIO with
# force_terminal=True still emits real ANSI/box-drawing output, deterministically.
# ---------------------------------------------------------------------------


def test_split_completed_markdown_keeps_only_the_trailing_block_live():
    committed, trailing = _split_completed_markdown("first paragraph\n\nsecond")

    assert committed == "first paragraph\n\n"
    assert trailing == "second"


def test_split_completed_markdown_does_not_split_a_fenced_code_block():
    committed, trailing = _split_completed_markdown("```python\n\nprint('hi')\n```")

    assert committed == ""
    assert trailing == "```python\n\nprint('hi')\n```"


def test_rich_reply_commits_a_completed_block_without_repeating_the_live_tail():
    """The completed paragraph becomes static; only ``second`` is live.

    A non-terminal console gives us the final bytes without cursor-control
    redraws, making this a public-output regression test rather than a check of
    the renderer's private Live state.
    """
    console = _rich_console(force_terminal=False)
    rich = RichRenderer(console=console)

    rich.reply_delta("first paragraph")
    rich.reply_delta("\n\nsecond paragraph")
    rich.reply_end()

    output = console.file.getvalue()
    assert output.count("first paragraph") == 1
    assert output.count("second paragraph") == 1


def _rich_console(*, force_terminal: bool = True) -> Console:
    """``force_terminal=False`` simulates a pipe or redirect: an io.StringIO
    has no real tty either way, but leaving ``force_terminal`` at its default
    ``None`` would auto-detect off whatever ``isatty()`` says, which isn't
    deterministic across environments — pinning it explicitly is what makes
    the non-terminal tests below actually exercise the non-terminal path."""
    return Console(file=io.StringIO(), force_terminal=force_terminal, width=60)


def test_rich_reply_renders_markdown_instead_of_literal_syntax():
    console = _rich_console()
    rich = RichRenderer(console=console)

    rich.reply_delta("**bold** and `code`")
    rich.reply_end()

    out = console.file.getvalue()
    assert "**bold**" not in out
    assert "`code`" not in out
    assert "bold" in out and "code" in out


def test_rich_reply_is_bounded_by_a_left_gutter_bar():
    console = _rich_console()
    rich = RichRenderer(console=console)

    rich.reply_delta("hello")
    rich.reply_end()

    assert "┃" in console.file.getvalue()


def test_rich_reply_end_ends_with_exactly_one_newline_off_a_terminal():
    """Regression: rich.live_render.LiveRender never emits a trailing newline
    after a live region's last row — Live.stop() is what closes that line,
    and it only does so when the console *is* a real terminal. Off one (a
    pipe, a log redirect — what force_terminal=False simulates here), the
    cursor was left mid-row, so whatever printed next landed glued onto the
    reply's last (blank, gutter-bottom) line instead of starting its own. A
    test that only checked the markdown body would have missed this
    entirely: it needs a non-terminal console to surface at all — the
    default helper here (force_terminal=True) never hit this path."""
    console = _rich_console(force_terminal=False)
    rich = RichRenderer(console=console)

    rich.reply_delta("- one\n- two\n")
    rich.reply_end()

    out = console.file.getvalue()
    assert out.endswith("\n")
    assert not out.endswith("\n\n")  # exactly one newline, not a blank line too

    # Simulate "whatever the CLI prints next" landing on the same stream.
    console.file.write("(saved as 'x')\n")
    assert console.file.getvalue().splitlines()[-1] == "(saved as 'x')"


def test_rich_reply_end_does_not_double_the_newline_on_a_real_terminal():
    """The other side of the same guarantee: Live.stop() already closes the
    line itself when the console is a real terminal, so the fix must not add
    a second one on top and leave a blank line behind."""
    console = _rich_console()  # force_terminal=True — the interactive path
    rich = RichRenderer(console=console)

    rich.reply_delta("hello")
    rich.reply_end()

    assert not console.file.getvalue().endswith("\n\n")


def test_rich_tool_call_also_ends_with_exactly_one_newline_off_a_terminal():
    """Same guarantee, mid-turn: tool_call finalises the live region before a
    tool runs, and the trace line that follows it — on stderr, but sharing
    the same terminal screen as stdout — must not land on the reply's tail
    either."""
    console = _rich_console(force_terminal=False)
    rich = RichRenderer(console=console)

    rich.reply_delta("partial reasoning before a tool call")
    rich.tool_call("echo", {"text": "hi"})

    out = console.file.getvalue()
    assert out.endswith("\n")
    assert not out.endswith("\n\n")

    console.file.write("  [tool] echo({'text': 'hi'}) -> hi\n")
    assert console.file.getvalue().splitlines()[-1] == "  [tool] echo({'text': 'hi'}) -> hi"


def test_rich_reply_break_before_tool_call_matches_tool_call_alone():
    """reply_break() has nothing to draw for Rich — tool_call (which loop.py
    always calls right after) already finalises the live region. Proven by
    comparing against a renderer that never received the reply_break call at
    all: if reply_break did anything visible (like the synthetic "\\n" delta
    it replaces, which used to leak into the markdown buffer), this would
    diverge."""
    with_break = _rich_console(force_terminal=False)
    rich_with_break = RichRenderer(console=with_break)
    rich_with_break.reply_delta("partial reasoning before a tool call")
    rich_with_break.reply_break()
    rich_with_break.tool_call("echo", {"text": "hi"})

    without_break = _rich_console(force_terminal=False)
    rich_without_break = RichRenderer(console=without_break)
    rich_without_break.reply_delta("partial reasoning before a tool call")
    rich_without_break.tool_call("echo", {"text": "hi"})

    assert with_break.file.getvalue() == without_break.file.getvalue()


def test_rich_tool_call_closes_the_live_region_so_the_next_reply_starts_fresh():
    """tool_call's whole reason to exist: hand the screen back before a tool
    runs. Proven behaviourally, through the public interface, rather than by
    reaching into the renderer's state: if the region weren't closed and the
    buffer weren't reset, the next reply would render as the concatenation of
    both fragments instead of as its own fresh panel."""
    console = _rich_console()
    rich = RichRenderer(console=console)

    rich.reply_delta("first")
    rich.tool_call("echo", {"text": "hi"})
    rich.reply_delta("second")
    rich.reply_end()

    out = console.file.getvalue()
    assert "first" in out
    assert "second" in out
    assert "firstsecond" not in out


def test_rich_reply_abort_emits_nothing(monkeypatch):
    console = _rich_console()
    rich = RichRenderer(console=console)

    rich.reply_delta("partial reply that never finishes")
    before = console.file.getvalue()
    assert before  # sanity: the in-progress reply did draw something

    rich.reply_abort()

    assert console.file.getvalue() == before  # abort added not one byte


def test_rich_reply_abort_with_no_reply_in_progress_emits_nothing():
    console = _rich_console()
    rich = RichRenderer(console=console)

    rich.reply_abort()

    assert console.file.getvalue() == ""


def test_rich_reply_abort_then_a_new_reply_still_renders_cleanly():
    """Regression for the abort path leaving the console's live-region
    bookkeeping unbalanced: a turn after an aborted one must still be able to
    open its own live region and render normally."""
    console = _rich_console()
    rich = RichRenderer(console=console)

    rich.reply_delta("first, aborted")
    rich.reply_abort()

    rich.reply_delta("**second**")
    rich.reply_end()

    out = console.file.getvalue()
    assert "**second**" not in out
    assert "second" in out


def test_rich_trace_uses_compact_glyphs_and_never_prints_raw_reasoning():
    reply_console = _rich_console()
    trace_console = _rich_console(force_terminal=False)
    rich = RichRenderer(console=reply_console, trace_console=trace_console)

    rich.step(2)
    rich.reasoning("think")
    rich.reasoning("ing")
    rich.reasoning_end()
    rich.tool_call("echo", {"text": "hi"})
    rich.tool_result("echo", {"text": "hi"}, "hi", is_error=False)
    rich.note("the model ran out of tokens; this turn is cut off")

    trace = trace_console.file.getvalue()
    assert "● thinking · step 2" in trace
    assert "● thinking · 8 chars · /reason" in trace
    assert "thinking" not in trace.replace("thinking ·", "")
    assert '● echo(text="hi")' in trace
    assert "⎿ hi" in trace
    assert "● the model ran out of tokens" in trace
    assert reply_console.file.getvalue() == ""  # trace never lands on the reply channel


def test_rich_trace_collapses_multiline_tool_output_to_one_line():
    trace_console = _rich_console(force_terminal=False)
    rich = RichRenderer(console=_rich_console(), trace_console=trace_console)

    rich.tool_result("read", {}, "first line\nsecond line", is_error=False)

    assert "⎿ first line second line" in trace_console.file.getvalue()


def test_rich_reasoning_can_stream_in_full(monkeypatch):
    monkeypatch.setattr("vegapunk.render.config", replace(style.config, reasoning="full"))
    trace_console = _rich_console(force_terminal=False)
    rich = RichRenderer(console=_rich_console(), trace_console=trace_console)

    rich.reasoning("the full thought")
    rich.reasoning_end()

    assert "⎿ the full thought" in trace_console.file.getvalue()


def test_pick_refuses_an_unknown_reasoning_mode():
    with pytest.raises(ValueError, match="VEGAPUNK_REASONING"):
        pick(replace(style.config, reasoning="summary"))
