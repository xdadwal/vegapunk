"""Tests for the renderer seam.

``PlainRenderer`` must print exactly what ``loop.py`` and ``cli.py`` printed
before the seam existed. These tests pin those bytes directly, so a drift shows
up here as well as in the display tests that consume it.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from vegapunk import style
from vegapunk.render import UI_MODES, PlainRenderer


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
                   "reply_delta", "reply_end"):
        assert callable(getattr(chosen, method)), f"{method} missing"
    assert isinstance(chosen, Renderer)  # runtime_checkable


@pytest.mark.parametrize("mode", UI_MODES)
def test_pick_returns_plain_for_every_known_mode_today(mode):
    """pick's actual contract, today: every known VEGAPUNK_UI value yields
    PlainRenderer — there's no other implementation yet (see pick's docstring).
    This is the thing that changes, and this test breaks, the day a rich
    renderer lands and 'auto'/'rich' start returning something else."""
    from vegapunk.render import pick

    assert isinstance(pick(replace(style.config, ui=mode)), PlainRenderer)


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
