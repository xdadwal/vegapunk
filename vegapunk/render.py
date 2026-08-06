"""What a turn looks like — the seam every printed byte goes through.

``loop.py`` decides *what* happened; this decides how it appears. Splitting them
is what lets the terminal UI change without touching the event handling, and
what lets a piped or scripted session keep the plain text it has always had.

Two channels, deliberately separate. Reply text goes to stdout so it can be
piped; everything else — steps, reasoning, tool results, warnings — goes to
stderr, so watching the loop work never contaminates the answer.
"""

from __future__ import annotations

import io
import sys
from typing import Protocol, runtime_checkable

from rich.box import Box
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel

from . import style
from .config import Config, config


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


@runtime_checkable
class Renderer(Protocol):
    """How a turn is shown. One instance spans both channels of one turn."""

    def step(self, number: int) -> None:
        """A model round trip began."""

    def reasoning(self, text: str) -> None:
        """A fragment of the model's chain of thought arrived."""

    def reasoning_end(self) -> None:
        """No more reasoning this turn. Idempotent — callers needn't check."""

    def tool_call(self, name: str, arguments: dict) -> None:
        """A tool was requested — it has not run yet.

        Separate from :meth:`tool_result` because the gap between them is where
        a tool actually runs, and where a human may be answering an approval
        prompt. A renderer that owns part of the screen has to hand it back
        before that wait begins; being told only at ``tool_result`` would mean
        finding out after the fact.

        ``PlainRenderer`` ignores it — it prints the arguments beside the
        result, so it has nothing to say yet.
        """

    def tool_result(self, name: str, arguments: dict, content: str, is_error: bool) -> None:
        """A tool ran and produced ``content``."""

    def note(self, text: str) -> None:
        """A warning about the loop itself, not about the model's answer."""

    def reply_delta(self, text: str) -> None:
        """A fragment of the assistant's answer arrived."""

    def reply_break(self) -> None:
        """The model spoke and is about to call a tool in the same turn: the
        spoken line needs to end before the tool trace starts.

        This is layout, not content — it exists so ``loop.py`` never has to
        fake it by yielding a synthetic ``"\\n"`` ``TextDelta``, which is what
        it did before this method existed. That synthetic delta traveled up
        indistinguishable from real model speech, so ``PlainRenderer`` (which
        prints reply text as bytes) happened to do the right thing with it,
        while ``RichRenderer`` (which re-renders the buffered text as
        markdown) had a stray newline baked into the model's prose to force a
        purely visual split. Same fix shape as ``tool_call`` existing
        separately from ``tool_result``: tell the renderer what happened and
        let it decide how to show it, rather than encoding the effect into
        content every renderer has to reinterpret.
        """

    def reply_end(self) -> None:
        """The answer is complete. NOT idempotent — a second call prints another
        prompt line, so callers must call it exactly once per turn that reaches
        this point."""

    def reply_abort(self) -> None:
        """The turn ended abnormally (interrupted, cancelled, or failed) before
        ``reply_end`` ran. Resets any in-progress reply state *without* printing
        anything, so the next turn starts clean.

        Exists because a renderer instance now lives for the whole session
        instead of being a fresh local per turn: without this, a turn that never
        reaches ``reply_end`` (Ctrl-C mid-reply, Ctrl-C at an approval prompt, a
        raised exception) leaves ``_spoke``/``_line_open`` set, and the *next*
        turn's first delta silently skips its ``vega> `` prefix. A bare
        ``reply_end`` call in a ``finally`` isn't a substitute: on the abnormal
        path it would print the pending newline itself, and the caller's own
        "(interrupted)" message would then land after a blank line the old
        code never produced.
        """


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

    def tool_call(self, name: str, arguments: dict) -> None:
        """Nothing to print yet: the arguments go out beside the result, on one
        line, so there is no half-written line to close before the tool runs."""

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

    def reply_break(self) -> None:
        # Reproduces exactly what the synthetic "\n" delta this replaces used
        # to print via reply_delta: an unconditional newline, leaving _spoke
        # untouched (so a later reply_delta this turn skips the "vega> "
        # prefix) and _line_open cleared (so reply_end doesn't add its own).
        print(flush=True)
        self._line_open = False

    def reply_end(self) -> None:
        if not self._spoke:
            print(self._prefix())  # an empty reply still gets its prompt line
        elif self._line_open:
            print()
        self._spoke = False
        self._line_open = False

    def reply_abort(self) -> None:
        self._spoke = False
        self._line_open = False

    @staticmethod
    def _prefix() -> str:
        """Punk Records speaking. The reset lands before the space so the reply
        itself streams in the default colour."""
        return style.paint("vega>", style.BOLD + style.MAGENTA, sys.stdout) + " "


# A Box whose only visible edge is the left one. Panel draws its top and
# bottom borders from the first/last lines below (all spaces, so those rows
# come out blank) and its content rows from the "mid" line's left/right
# chars — left is the bar, right is a blank padding column — so a reply reads
# as a bounded block with a gutter down the left side instead of a full box.
# The other lines (head/head_row/row/foot_row/foot) are never read by Panel's
# renderer; they're filled in only because Box requires all eight.
_GUTTER_BOX = Box(
    "    \n"
    "┃   \n"
    "    \n"
    "┃   \n"
    "    \n"
    "    \n"
    "┃   \n"
    "    \n"
)


class RichRenderer:
    """Styled reply body on stdout: the markdown is re-rendered live, in
    place, behind a left-edge gutter, token by token. Everything else — the
    trace on stderr — is unchanged: delegated verbatim to an internal
    ``PlainRenderer``, so restyling the reply never touches the watch
    channel. See the module docstring for why the two channels stay split.
    """

    def __init__(self, console: Console | None = None) -> None:
        self._console = console if console is not None else Console(file=sys.stdout)
        self._plain = PlainRenderer()  # trace only — see class docstring
        self._buffer = ""
        self._live: Live | None = None

    # -- the watch channel (stderr) — delegated verbatim ---------------------

    def step(self, number: int) -> None:
        self._plain.step(number)

    def reasoning(self, text: str) -> None:
        self._plain.reasoning(text)

    def reasoning_end(self) -> None:
        self._plain.reasoning_end()

    def tool_result(self, name: str, arguments: dict, content: str, is_error: bool) -> None:
        self._plain.tool_result(name, arguments, content, is_error)

    def note(self, text: str) -> None:
        self._plain.note(text)

    def tool_call(self, name: str, arguments: dict) -> None:
        """A tool is about to run — or a human is about to answer an approval
        prompt — so the region that's been redrawing in place has to give the
        screen back before that wait begins. Finalise whatever's live so far;
        a later ``reply_delta`` opens a fresh region."""
        self._finish_live()

    # -- the answer channel (stdout) -----------------------------------------

    def reply_delta(self, text: str) -> None:
        if not text:
            return
        self._buffer += text
        if self._live is None:
            self._live = Live(
                console=self._console,
                # A tight delta loop never yields to the background thread
                # auto_refresh relies on to redraw, which makes Live look
                # inert. Redraw explicitly on every delta instead.
                auto_refresh=False,
                # A live region can only redraw what's still on screen; once
                # a long reply scrolls the top of the terminal, Live loses
                # track of it. Crop instead of letting it scroll, so the
                # prompt above can never be pushed out of reach.
                vertical_overflow="crop",
                transient=False,
                # Nothing else writes to stdout/stderr while a reply is live
                # (tool_call closes the region before a tool's own output can
                # appear), so there's nothing for Live to intercept.
                redirect_stdout=False,
                redirect_stderr=False,
            )
            self._live.start()
        self._live.update(self._panel(), refresh=True)

    def reply_break(self) -> None:
        """Nothing to draw: ``loop.py`` calls this immediately before
        ``tool_call``, and ``tool_call`` already finalises the live region on
        its way to giving the screen back for the tool trace. Finalising here
        too would just be the same work twice — ``_finish_live`` is written
        to tolerate that, but there's no reason to lean on it. What this
        method replaces was letting a synthetic newline into ``self._buffer``,
        which then got re-rendered as part of the model's markdown; not
        touching the buffer at all is the fix.
        """

    def reply_end(self) -> None:
        self._finish_live()

    def reply_abort(self) -> None:
        """Reset without drawing anything: an interrupted turn must leave no
        trace, but ``Live.stop()`` always performs one last refresh plus a
        cursor-show, and there's no public flag to suppress that. Pointing
        the console at a scratch buffer for the call (then restoring it) is
        the only way to run its real teardown — so the console's live slot
        and render-hook stack stay correctly balanced for the next turn —
        without those bytes reaching the actual stream."""
        if self._live is not None:
            scratch, real_file = io.StringIO(), self._console.file
            self._console.file = scratch
            try:
                self._live.stop()
            finally:
                self._console.file = real_file
        self._live = None
        self._buffer = ""

    def _panel(self) -> Panel:
        return Panel(Markdown(self._buffer), box=_GUTTER_BOX, border_style="magenta")

    def _finish_live(self) -> None:
        if self._live is not None:
            # rich.live_render.LiveRender never emits a trailing newline
            # after the region's last row — Live.stop() is what closes that
            # line, and it only does so when console.is_terminal is True (a
            # real terminal owns its own cursor; a file or pipe doesn't need
            # one repositioned). Off a terminal — piped output, or a plain
            # redirect — stop() leaves the cursor mid-row, and whatever
            # prints next (the "[tool]" trace line on stderr, sharing the
            # same screen; "(saved as ...)" on stdout) lands glued onto the
            # reply. Checked before stop() runs, since stop() flips this same
            # is_terminal-gated behaviour internally and we don't want to
            # double up on a real terminal, where it already closes the line.
            needs_newline = not self._console.is_terminal
            self._live.stop()
            if needs_newline:
                self._console.line()
        self._live = None
        self._buffer = ""


UI_MODES = ("auto", "rich", "plain")


def pick(cfg: Config = config) -> Renderer:
    """The renderer for this process.

    ``rich`` always chooses :class:`RichRenderer` — an explicit request, so it
    wins even off a real terminal (piped to a file, say). ``auto`` follows the
    terminal: ``RichRenderer`` only when stdout is an actual, unredirected
    one, ``PlainRenderer`` otherwise — a pipe, a log redirect, or a test.
    ``plain`` always chooses ``PlainRenderer``.

    ``auto``'s check is ``stdout.isatty()`` *and* ``style.enabled(stdout)``,
    not the latter alone, even though ``style.enabled`` already knows about
    ``NO_COLOR`` and TTY detection and is reused here rather than
    reimplemented: ``style.enabled`` alone says yes for ``VEGAPUNK_COLOR=
    always`` regardless of ``isatty()``, which makes sense for colour codes
    surviving a pipe into ``less -R`` but not for a live, cursor-addressed
    redraw that needs a real terminal underneath it. The explicit ``isatty()``
    is what keeps that case honest; ``style.enabled`` still supplies the
    ``NO_COLOR``/``VEGAPUNK_COLOR=never`` opt-out on top of it.

    Raises:
        ValueError: If ``VEGAPUNK_UI`` is not a known mode. Said out loud rather
            than defaulting, because silently ignoring the setting is how you
            spend an afternoon wondering why it does nothing.
    """
    if cfg.ui not in UI_MODES:
        raise ValueError(
            f"Unknown VEGAPUNK_UI {cfg.ui!r} — expected one of: {', '.join(UI_MODES)}."
        )
    if cfg.ui == "rich" or (
        cfg.ui == "auto" and sys.stdout.isatty() and style.enabled(sys.stdout)
    ):
        return RichRenderer()
    return PlainRenderer()
