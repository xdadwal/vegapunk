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
