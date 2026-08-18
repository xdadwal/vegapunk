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

from collections.abc import Callable
from dataclasses import dataclass

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.processors import BeforeInput
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


@dataclass(frozen=True)
class InlineEditor:
    """A text field opened for the highlighted option without leaving a menu.

    ``on_submit`` receives the option value and the editor text when the user
    presses Enter in the field. It determines the result returned by the menu.
    """

    key: str
    label: str
    prompt: str
    on_submit: Callable[[str, str], object]


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
    selected_value: str | None = None,
    inline_editor: InlineEditor | None = None,
) -> Application:
    """The picker as an Application; ``.run()`` yields a value or ``None``.

    Split from :func:`choose` so tests can drive the real widget through a
    prompt_toolkit pipe and a DummyOutput rather than a terminal.

    Args:
        title: A heading, plain or pre-styled.
        options: The rows, in display order. Must not be empty.
        input: Test seam — a prompt_toolkit input pipe.
        output: Test seam — usually a DummyOutput.
        selected_value: Value to place under the cursor when the menu opens.
            Falls back to the active row, then the first row.
        inline_editor: Optional text editor opened on the highlighted row.

    Returns:
        An Application returning the chosen ``Option.value``, an inline-editor
        result, or ``None`` if the user backed out.

    Raises:
        ValueError: If ``options`` is empty — a menu with nothing to pick is a
            caller bug, and rendering it would hang on input that can't matter.
    """
    if not options:
        raise ValueError("a menu needs at least one option")

    header: FormattedText = [("bold", f"{title}\n")] if isinstance(title, str) else list(title)
    # Start on an explicit value when reopening a menu, otherwise on the active
    # row so settings menus land on their current value rather than making the
    # user find it.
    state = {
        "idx": next(
            (
                i
                for i, option in enumerate(options)
                if option.value == selected_value
            ),
            next((i for i, option in enumerate(options) if option.active), 0),
        ),
        "editing": False,
    }
    editor_buffer = Buffer(multiline=False)
    editor_control = BufferControl(
        buffer=editor_buffer,
        input_processors=[
            BeforeInput(
                lambda: f"  {inline_editor.prompt} for {options[state['idx']].label} > ",
                style="class:dim",
            )
        ],
    )

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
        if state["editing"]:
            lines.append(("class:dim", "  enter submit note · esc/c-c return to options"))
        else:
            hint = "  ↑↓ move · enter select"
            if inline_editor is not None:
                hint += f" · {inline_editor.label}"
            lines.append(("class:dim", hint + " · esc cancel"))
        return lines

    kb = KeyBindings()

    @kb.add("up", filter=Condition(lambda: not state["editing"]))
    def _(event) -> None:
        state["idx"] = (state["idx"] - 1) % len(options)

    @kb.add("down", filter=Condition(lambda: not state["editing"]))
    def _(event) -> None:
        state["idx"] = (state["idx"] + 1) % len(options)

    @kb.add("enter", filter=Condition(lambda: not state["editing"]), eager=True)
    def _(event) -> None:
        event.app.exit(result=options[state["idx"]].value)

    if inline_editor is not None:

        @kb.add(inline_editor.key, filter=Condition(lambda: not state["editing"]), eager=True)
        def _(event) -> None:
            editor_buffer.reset(document=Document(""))
            state["editing"] = True
            event.app.layout.focus(editor_control)

        @kb.add("enter", filter=Condition(lambda: state["editing"]), eager=True)
        def _(event) -> None:
            event.app.exit(
                result=inline_editor.on_submit(
                    options[state["idx"]].value, editor_buffer.text.strip()
                )
            )

        @kb.add("escape", filter=Condition(lambda: state["editing"]), eager=True)
        @kb.add("c-c", filter=Condition(lambda: state["editing"]), eager=True)
        def _(event) -> None:
            state["editing"] = False
            event.app.layout.focus(control)

    # Both back out with no selection. Ctrl-C is the habit; Esc is what the hint
    # line advertises. Neither raises — a cancelled menu is an ordinary outcome,
    # not an interrupt the REPL should treat as cancelling the turn.
    @kb.add("escape", filter=Condition(lambda: not state["editing"]), eager=True)
    @kb.add("c-c", filter=Condition(lambda: not state["editing"]))
    def _(event) -> None:
        event.app.exit(result=None)

    control = FormattedTextControl(render, focusable=True, show_cursor=False)
    # Header + a viewport's worth of rows + the hint, plus the two scroll
    # markers when the list is long enough to need them.
    height = len(header) + min(len(options), VISIBLE) + (3 if len(options) > VISIBLE else 1)
    editor = (
        ConditionalContainer(
            Window(editor_control, height=1),
            filter=Condition(lambda: state["editing"]),
        )
        if inline_editor is not None
        else Window(height=0)
    )
    return Application(
        layout=Layout(HSplit([Window(control, height=height), editor]), focused_element=control),
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
    selected_value: str | None = None,
    inline_editor: InlineEditor | None = None,
) -> object | None:
    """Run the picker and return a selection, editor result, or ``None``.

    Normal menus return an option value. A caller that supplies an
    ``inline_editor`` receives its result after editing the highlighted option.
    """
    return build(
        title,
        options,
        input=input,
        output=output,
        selected_value=selected_value,
        inline_editor=inline_editor,
    ).run()
