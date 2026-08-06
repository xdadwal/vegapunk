"""Tests for the inline picker — the real widget, driven through a pipe.

``vegapunk/menu.py`` is the one selection UI: the approval prompt and the
``/model``, ``/models``, ``/load``, ``/skill`` and ``/effort`` pickers all run
it, so what's pinned here is the interaction contract they share — where the
cursor starts, what the keys do, and that backing out is an ordinary answer
rather than an interrupt.

Driven with a prompt_toolkit pipe and a ``DummyOutput``, so the keystrokes are
real and nothing is drawn.
"""

from __future__ import annotations

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from vegapunk.menu import VISIBLE, Option, _viewport, build

DOWN = "\x1b[B"
UP = "\x1b[A"
ENTER = "\r"
ESC = "\x1b\x1b"  # doubled: a lone ESC is the prefix of every arrow-key sequence


def _run(options: list[Option], keys: str) -> str | None:
    with create_pipe_input() as inp:
        inp.send_text(keys)
        return build("pick one", options, input=inp, output=DummyOutput()).run()


def _three() -> list[Option]:
    return [Option(value="a", label="A"), Option(value="b", label="B"), Option(value="c", label="C")]


def test_enter_chooses_the_row_under_the_cursor():
    assert _run(_three(), ENTER) == "a"


def test_down_then_enter_moves_one_row():
    assert _run(_three(), DOWN + ENTER) == "b"


def test_up_from_the_top_wraps_to_the_bottom():
    assert _run(_three(), UP + ENTER) == "c"


def test_the_cursor_starts_on_the_active_row():
    """Opening a menu lands on what's already set, so changing a setting never
    means hunting for the current value first."""
    options = [
        Option(value="a", label="A"),
        Option(value="b", label="B", active=True),
        Option(value="c", label="C"),
    ]

    assert _run(options, ENTER) == "b"


def test_escape_backs_out_without_choosing():
    assert _run(_three(), ESC) is None


def test_ctrl_c_backs_out_without_raising():
    """A dismissed menu is an ordinary outcome. Letting KeyboardInterrupt escape
    would reach the REPL's handler and cancel the whole turn instead."""
    assert _run(_three(), "\x03") is None


def test_a_menu_with_nothing_to_pick_is_a_caller_bug():
    with pytest.raises(ValueError, match="at least one option"):
        build("pick one", [])


def test_details_do_not_change_what_is_returned():
    options = [Option(value="a", label="A", detail="some context")]

    assert _run(options, ENTER) == "a"


# ---------------------------------------------------------------------------
# the scrolling viewport
# ---------------------------------------------------------------------------


def test_a_short_list_is_shown_whole():
    assert _viewport(0, 5) == (0, 5)


def test_a_long_list_scrolls_to_keep_the_selection_visible():
    total = VISIBLE + 10
    start, end = _viewport(total - 1, total)

    assert end - start == VISIBLE
    assert start <= total - 1 < end


def test_the_viewport_clamps_at_both_ends():
    """Otherwise the first and last screens would be half empty, and a menu that
    is taller than the terminal would push the prompt off-screen."""
    total = VISIBLE + 10

    assert _viewport(0, total) == (0, VISIBLE)
    assert _viewport(total - 1, total) == (total - VISIBLE, total)


def test_a_long_list_is_navigable_end_to_end():
    options = [Option(value=str(i), label=f"row {i}") for i in range(VISIBLE + 5)]

    assert _run(options, UP + ENTER) == str(len(options) - 1)  # wraps past the viewport
