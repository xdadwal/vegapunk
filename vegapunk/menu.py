"""The inline arrow-key picker — Vegapunk's one selection widget.

Extracted from the approval prompt, which was the first thing that needed to
ask a question with a fixed set of answers. ``/model``, ``/models``, ``/load``,
``/skill`` and ``/effort`` are the rest, so the menu lives here and every one of
them looks and behaves the same: Up/Down to move, Enter to choose, Esc or Ctrl-C
to back out.

Inline rather than full-screen, and ``erase_when_done``, so the picker vanishes
after you choose and the scrollback keeps only what you did. That is what lets a
selection UI coexist with a REPL whose replies stream to stdout and whose trace
streams to stderr — taking over the screen would mean owning both.

Long lists scroll inside a fixed viewport instead of growing without limit: a
menu taller than the terminal would push the prompt off-screen, and the model
list for a Claude subscription is already ten entries.
"""

from __future__ import annotations

from dataclasses import dataclass

from prompt_toolkit.application import Application
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import Output

# How many options are visible at once. Beyond this the list scrolls under the
# selection. Ten fits a Claude model list; twelve leaves room without crowding a
# short terminal.
VISIBLE = 12

# prompt_toolkit formatted text: a list of (style, text) pairs.
FormattedText = list[tuple[str, str]]


@dataclass(frozen=True)
class Option:
    """One row of a menu.

    Attributes:
        value: What ``choose`` returns when this row is picked.
        label: The row's name, shown first and in normal weight.
        detail: Dimmed context after the label — a credential kind, a turn
            count, a skill's summary. Never load-bearing: a reader who ignores
            it entirely can still tell the options apart.
        active: Marks the row as the current setting, so a menu doubles as a
            display of what is already selected.
    """

    value: str
    label: str
    detail: str = ""
    active: bool = False


def _viewport(index: int, total: int) -> tuple[int, int]:
    """The slice of options to draw so ``index`` is always inside it.

    Keeps the selection roughly centred once the list is long enough to scroll,
    and clamps at both ends so the first and last screens are full rather than
    half-empty.
    """
    if total <= VISIBLE:
        return 0, total
    start = min(max(index - VISIBLE // 2, 0), total - VISIBLE)
    return start, start + VISIBLE


def build(
    title: str | FormattedText,
    options: list[Option],
    *,
    input: Input | None = None,
    output: Output | None = None,
) -> Application:
    """The picker as an Application; ``.run()`` yields a value or ``None``.

    Split from :func:`choose` so tests can drive the real widget through a
    prompt_toolkit pipe and a DummyOutput rather than a terminal.

    Args:
        title: A heading, plain or pre-styled.
        options: The rows, in display order. Must not be empty.
        input: Test seam — a prompt_toolkit input pipe.
        output: Test seam — usually a DummyOutput.

    Returns:
        An Application returning the chosen ``Option.value``, or ``None`` if the
        user backed out.

    Raises:
        ValueError: If ``options`` is empty — a menu with nothing to pick is a
            caller bug, and rendering it would hang on input that can't matter.
    """
    if not options:
        raise ValueError("a menu needs at least one option")

    header: FormattedText = [("bold", f"{title}\n")] if isinstance(title, str) else list(title)
    # Start on the active row, so opening a menu lands on what's already set
    # rather than making you find it.
    state = {"idx": next((i for i, o in enumerate(options) if o.active), 0)}

    def render() -> FormattedText:
        lines: FormattedText = list(header)
        start, end = _viewport(state["idx"], len(options))
        if start:
            lines.append(("class:dim", f"  ⋯ {start} more above\n"))
        for i in range(start, end):
            option = options[i]
            selected = i == state["idx"]
            style = "reverse" if selected else ""
            mark = "●" if option.active else " "
            lines.append((style, f"{'❯' if selected else ' '} {mark} {option.label}"))
            if option.detail:
                # Dim only when this row isn't selected: `reverse` already
                # inverts the row, and dimming on top of it is unreadable.
                lines.append((style or "class:dim", f"  {option.detail}"))
            lines.append((style, "\n"))
        if end < len(options):
            lines.append(("class:dim", f"  ⋯ {len(options) - end} more below\n"))
        lines.append(("class:dim", "  ↑↓ move · enter select · esc cancel"))
        return lines

    kb = KeyBindings()

    @kb.add("up")
    def _(event) -> None:
        state["idx"] = (state["idx"] - 1) % len(options)

    @kb.add("down")
    def _(event) -> None:
        state["idx"] = (state["idx"] + 1) % len(options)

    @kb.add("enter")
    def _(event) -> None:
        event.app.exit(result=options[state["idx"]].value)

    # Both back out with no selection. Ctrl-C is the habit; Esc is what the hint
    # line advertises. Neither raises — a cancelled menu is an ordinary outcome,
    # not an interrupt the REPL should treat as cancelling the turn.
    @kb.add("escape", eager=True)
    @kb.add("c-c")
    def _(event) -> None:
        event.app.exit(result=None)

    control = FormattedTextControl(render, focusable=True, show_cursor=False)
    # Header + a viewport's worth of rows + the hint, plus the two scroll
    # markers when the list is long enough to need them.
    height = len(header) + min(len(options), VISIBLE) + (3 if len(options) > VISIBLE else 1)
    return Application(
        layout=Layout(HSplit([Window(control, height=height)])),
        key_bindings=kb,
        full_screen=False,
        erase_when_done=True,
        input=input,
        output=output,
    )


def choose(
    title: str | FormattedText,
    options: list[Option],
    *,
    input: Input | None = None,
    output: Output | None = None,
) -> str | None:
    """Run the picker and return the chosen value, or ``None`` if cancelled."""
    return build(title, options, input=input, output=output).run()
