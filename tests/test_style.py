"""Tests for the color seam — deterministic, no TTY required.

``enabled`` composes three inputs: the VEGAPUNK_COLOR mode, the NO_COLOR
standard, and the stream's own isatty(). These pin the precedence (explicit
"always" beats NO_COLOR; NO_COLOR beats auto-detection) and that a disabled
``paint`` returns byte-identical text — the property the rest of the suite's
plain-substring assertions stand on.
"""

from __future__ import annotations

import io
from dataclasses import replace

from vegapunk import style


class _Stream(io.StringIO):
    """A stream whose ttyness the test controls."""

    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _mode(monkeypatch, color: str) -> None:
    monkeypatch.setattr("vegapunk.style.config", replace(style.config, color=color))


def test_auto_enables_only_on_a_tty(monkeypatch):
    _mode(monkeypatch, "auto")
    assert style.enabled(_Stream(tty=True)) is True
    assert style.enabled(_Stream(tty=False)) is False


def test_no_color_disables_even_on_a_tty(monkeypatch):
    _mode(monkeypatch, "auto")
    monkeypatch.setenv("NO_COLOR", "1")
    assert style.enabled(_Stream(tty=True)) is False


def test_never_disables_even_on_a_tty(monkeypatch):
    _mode(monkeypatch, "never")
    assert style.enabled(_Stream(tty=True)) is False


def test_always_enables_even_when_piped(monkeypatch):
    _mode(monkeypatch, "always")
    assert style.enabled(_Stream(tty=False)) is True


def test_always_beats_no_color(monkeypatch):
    # The app-specific opt-in is more deliberate than the blanket standard.
    _mode(monkeypatch, "always")
    monkeypatch.setenv("NO_COLOR", "1")
    assert style.enabled(_Stream(tty=True)) is True


def test_paint_wraps_with_code_and_reset_when_enabled(monkeypatch):
    _mode(monkeypatch, "always")
    out = style.paint("hi", style.BOLD + style.MAGENTA, _Stream(tty=False))
    assert out == f"{style.BOLD}{style.MAGENTA}hi{style.RESET}"


def test_paint_returns_identical_text_when_disabled(monkeypatch):
    # Byte-identical, not merely escape-free — the plain-substring tests
    # elsewhere in the suite depend on it.
    _mode(monkeypatch, "never")
    assert style.paint("hi", style.CYAN, _Stream(tty=True)) == "hi"


def test_missing_isatty_counts_as_not_a_tty(monkeypatch):
    # Some file-like objects (e.g. io wrappers in tests) have no isatty at
    # all; treat them like pipes rather than crashing.
    _mode(monkeypatch, "auto")

    class _Bare:
        pass

    assert style.enabled(_Bare()) is False
