"""ClaudeBrain — a Brain backed by subscription-billed Claude.

Talks to Claude through ``claude-agent-sdk``, which drives the Claude Code CLI
bundled inside the package as a subprocess. That indirection is the point: a
Claude Pro/Max subscription only covers official clients, and headless Claude
Code (auth from ``claude /login`` or CLAUDE_CODE_OAUTH_TOKEN) is the sanctioned
way to spend it from a program. The raw ``anthropic`` SDK is API-key-only.

Vegapunk's loop stays in charge. Every Claude Code built-in tool is disabled
and each ``think`` is a single stateless turn: the whole OpenAI-shaped history
is rendered into one prompt (Vegapunk owns history, including Ctrl-C rollback,
so there is no server-side session to drift out of sync). Claude requests
Vegapunk tools by ending its reply with a fenced ``vega_tool`` JSON block,
which is parsed back into the same neutral ``ToolCall``/``BrainResponse``
shapes ``DMRBrain`` produces — downstream, nothing knows the difference.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import queue
import sys
import threading
import uuid
from collections.abc import Iterator
from typing import cast, get_args

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    CLINotFoundError,
    EffortLevel,
    ResultMessage,
    StreamEvent,
    TextBlock,
    query,
)

from . import style
from .brain import Brain, BrainResponse, ReasoningDelta, TextDelta, ThinkEvent, ToolCall
from .config import Config, config

_AUTH_HINT = (
    "If this is an authentication problem: run `claude /login` once on this "
    "machine, or set CLAUDE_CODE_OAUTH_TOKEN (create one with `claude setup-token`)."
)

# The SDK's own level list, so /effort and the env validation can never drift
# from what the CLI accepts.
EFFORT_LEVELS: tuple[str, ...] = get_args(EffortLevel)


def _validate_effort(level: str) -> EffortLevel:
    if level not in EFFORT_LEVELS:
        raise ValueError(
            f"Unknown effort level {level!r} — expected one of: {', '.join(EFFORT_LEVELS)}."
        )
    return cast(EffortLevel, level)


def _render_transcript(messages: list[dict]) -> tuple[str, str]:
    """Split OpenAI-shaped history into (system text, rendered transcript).

    The transcript is a labeled replay of every non-system turn — user text,
    assistant text, the assistant's tool calls (verbatim argument JSON), and
    tool results keyed by call id — so a stateless model can pick the
    conversation up mid-stride. Known v1 limitation: marker-like lines inside
    tool output are not escaped, so a tool result that itself contains
    ``[user]`` on its own line could read as a turn boundary.
    """
    system_text = ""
    turns = messages
    if messages and messages[0].get("role") == "system":
        system_text = messages[0].get("content") or ""
        turns = messages[1:]

    parts: list[str] = []
    for message in turns:
        role = message.get("role")
        content = message.get("content")
        if role == "assistant":
            if content:
                parts.append(f"[assistant]\n{content}")
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                parts.append(
                    f"[assistant called tool {function.get('name')} "
                    f"(call id: {call.get('id')}) with arguments:]\n"
                    f"{function.get('arguments')}"
                )
        elif role == "tool":
            parts.append(f"[tool result for call {message.get('tool_call_id')}]\n{content}")
        else:  # user (and anything unexpected renders as-is rather than vanishing)
            parts.append(f"[{role}]\n{content}")
    return system_text, "\n\n".join(parts)


# Appended to the system prompt every call: how to read the transcript and
# what shape the reply must take.
_CONTINUATION_FRAMING = (
    "The user message you receive is a transcript of the conversation so far, "
    "as labeled turns like [user], [assistant], and [tool result for call ...]. "
    "You are the assistant. Write the assistant's next turn only — no [assistant] "
    "label, no transcript markup, just the reply itself."
)


_FENCE_TAG = "vega_tool"

# A fence opens on a line that is exactly this, and closes on a line that is
# exactly ``` — entire lines, so ordinary code blocks stream through untouched.
_OPENER_LINE = f"```{_FENCE_TAG}"
_CLOSER_LINE = "```"


def _parse_call(body: str) -> dict | None:
    """Parse a fence body into {"name", "arguments"}, or None if malformed."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        return None
    arguments = data.get("arguments", {})
    if not isinstance(arguments, dict):
        return None
    return {"name": data["name"], "arguments": arguments}


class _FenceFilter:
    """Split a streamed reply into visible text and ``vega_tool`` calls.

    ``feed`` returns the text safe to display now; ``finish`` flushes whatever
    the end of the stream disambiguates. The only text ever held back is a
    line-start fragment that could still become the opener line (at most the
    opener's ~12 characters), so ordinary prose streams through live.

    Ordinary code blocks stream through untouched — including a ``vega_tool``
    fence quoted *inside* one (e.g. the model showing its own tool syntax as
    an example): a line opening any other fence turns the filter into a
    passthrough until that block's closing ``` line, so quoted examples are
    displayed, never executed. (A block opened with four-plus backticks whose
    body contains a bare ``` line ends the passthrough early — the cost is a
    real call after it rendering as visible text, a failure you can see,
    rather than a quoted example silently running, which you can't.)

    A fence that closes but doesn't parse (bad JSON, missing name) is re-emitted
    verbatim as visible text rather than dropped: a silently swallowed tool
    request would leave the user staring at a reply that just... ends. Same for
    a fence still open when the stream ends.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._tail = ""  # unprocessed input: a partial line (or held opener prefix)
        self._line_emitted = ""  # what we've already emitted of the current line
        self._in_fence = False  # inside a vega_tool fence (suppressing)
        self._in_code = False  # inside an ordinary code block (passthrough)
        self._fence_body: list[str] = []
        self._fence_raw = ""  # exact consumed fence text, for verbatim re-emit

    def feed(self, delta: str) -> str:
        out: list[str] = []
        buf = self._tail + delta
        self._tail = ""
        while buf:
            newline = buf.find("\n")
            if newline == -1:
                self._consume_partial(buf, out)
                break
            rest, buf = buf[:newline], buf[newline + 1 :]
            self._consume_line(self._line_emitted + rest, rest, out)
            self._line_emitted = ""
        return "".join(out)

    def _consume_partial(self, fragment: str, out: list[str]) -> None:
        """An incomplete line: hold it only where a vega opener could form."""
        if self._in_fence:
            self._tail = fragment
        elif not self._in_code and not self._line_emitted and _OPENER_LINE.startswith(fragment):
            self._tail = fragment  # could still become the opener line
        else:
            out.append(fragment)
            self._line_emitted += fragment

    def _consume_line(self, line: str, unemitted: str, out: list[str]) -> None:
        """A complete line: all state transitions happen here.

        ``line`` is the whole line (for decisions); ``unemitted`` is the part
        not yet streamed out (for emission).
        """
        if self._in_fence:
            self._fence_raw += line + "\n"
            if line == _CLOSER_LINE:
                self._close_fence(out)
            else:
                self._fence_body.append(line)
        elif self._in_code:
            out.append(unemitted + "\n")
            if line == _CLOSER_LINE:
                self._in_code = False
        elif line == _OPENER_LINE:
            self._in_fence = True
            self._fence_body = []
            self._fence_raw = line + "\n"
        else:
            out.append(unemitted + "\n")
            if line.startswith("```"):
                self._in_code = True  # some other fence: passthrough until it closes

    def _close_fence(self, out: list[str]) -> None:
        call = _parse_call("\n".join(self._fence_body))
        if call is None:
            out.append(self._fence_raw)  # malformed: shown, never swallowed
        else:
            self.calls.append(call)
        self._in_fence = False
        self._fence_body = []
        self._fence_raw = ""

    def finish(self) -> str:
        """End of stream: flush whatever the missing input leaves ambiguous."""
        out: list[str] = []
        if self._in_fence:
            if self._tail == _CLOSER_LINE:
                # Closer arrived without a trailing newline — still a close.
                self._fence_raw += self._tail
                self._close_fence(out)
            else:
                # Unterminated fence: everything it consumed goes back as text.
                out.append(self._fence_raw + self._tail)
        elif self._tail:
            out.append(self._tail)  # a held opener prefix that never resolved
        self._tail = ""
        return "".join(out)


def _tool_instructions(tools: list[dict]) -> str:
    """The tool-calling protocol stanza, rendered from OpenAI function schemas.

    Claude Code's own tools are disabled, so this prompt is the only tool
    channel: request one tool by ending the reply with a fenced ``vega_tool``
    JSON block, which ``_FenceFilter`` parses back out of the stream.
    """
    sections: list[str] = []
    for tool in tools:
        function = tool.get("function", {})
        sections.append(
            f"### {function.get('name')}\n"
            f"{function.get('description', '').strip()}\n"
            f"Arguments JSON schema: {json.dumps(function.get('parameters', {}))}"
        )
    catalog = "\n\n".join(sections)
    return (
        "## Requesting tools\n"
        "\n"
        "You cannot run tools yourself. To request one, end your reply with "
        f"exactly one fenced code block tagged {_FENCE_TAG}, containing a single "
        "JSON object:\n"
        "\n"
        f"```{_FENCE_TAG}\n"
        '{"name": "<tool name>", "arguments": {<arguments matching the tool\'s schema>}}\n'
        "```\n"
        "\n"
        "Rules:\n"
        f"- At most one {_FENCE_TAG} block per reply, as the very last thing in "
        "the reply.\n"
        "- After the block, stop. The result will come back as a "
        "[tool result ...] turn in the transcript.\n"
        "- When you have the final answer for the user, reply in plain text "
        f"with no {_FENCE_TAG} block.\n"
        "\n"
        "Available tools:\n"
        "\n"
        f"{catalog}"
    )


def _usage_tokens(usage: dict | None) -> int | None:
    """This call's total token footprint, from a ResultMessage usage dict.

    Input (including cache reads/writes) plus output — the same "how full is
    the context" number DMR reports as usage.total_tokens. None when the CLI
    didn't report usage.
    """
    if not usage:
        return None
    total = 0
    for key in (
        "input_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "output_tokens",
    ):
        value = usage.get(key)
        if isinstance(value, int):
            total += value
    return total or None


class ClaudeBrain(Brain):
    """A Brain backed by subscription-billed Claude via claude-agent-sdk.

    Construction is deliberately cheap — no subprocess, no client — so /model
    can swap it in instantly; a missing login surfaces on the first turn as a
    loud, actionable error instead.
    """

    def __init__(self, cfg: Config = config) -> None:
        self._model = cfg.claude_model  # "" means the Claude Code account default
        self._context_window = cfg.claude_context_window
        self._workspace_root = cfg.workspace_root
        # None means the SDK default ("high"). A junk VEGAPUNK_CLAUDE_EFFORT
        # fails loudly here — at launch, not mid-turn.
        self._effort: EffortLevel | None = (
            _validate_effort(cfg.claude_effort) if cfg.claude_effort else None
        )

    @property
    def model_label(self) -> str:
        return f"claude:{self._model}" if self._model else "claude"

    @property
    def context_window(self) -> int:
        return self._context_window

    @property
    def effort(self) -> str | None:
        """The current effort level; None means the SDK default ("high")."""
        return self._effort

    def set_effort(self, level: str) -> None:
        """Change the effort for subsequent turns (the /effort command)."""
        self._effort = _validate_effort(level)

    def think(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[ThinkEvent]:
        system_text, prompt = _render_transcript(messages)
        stanzas = [system_text] if system_text else []
        if tools:
            stanzas.append(_tool_instructions(tools))
        stanzas.append(_CONTINUATION_FRAMING)
        options = ClaudeAgentOptions(
            system_prompt="\n\n".join(stanzas),
            # A Vegapunk turn is one model reply; the loop drives everything else.
            max_turns=1,
            tools=[],  # no Claude Code built-ins — vega_tool fences are the only channel
            model=self._model or None,
            effort=self._effort,  # None = the SDK default ("high")
            include_partial_messages=True,  # stream text as it generates
            # Isolation: without these the CLI loads the user's real Claude Code
            # settings, CLAUDE.md, skills, and MCP servers into Vegapunk's turn.
            setting_sources=[],
            skills=[],
            strict_mcp_config=True,
            cwd=self._workspace_root,
        )

        fence = _FenceFilter()
        emitted: list[str] = []
        reasoning: list[str] = []
        fallback_text: list[str] = []
        result: ResultMessage | None = None
        truncated = False
        saw_text_delta = False

        def _show(visible: str) -> Iterator[TextDelta]:
            if visible:
                emitted.append(visible)
                yield TextDelta(visible)

        events = self._stream_query(prompt, options)
        try:
            for message in events:
                if isinstance(message, StreamEvent):
                    event = message.event
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            saw_text_delta = True
                            yield from _show(fence.feed(delta.get("text", "")))
                        elif delta.get("type") == "thinking_delta":
                            thought = delta.get("thinking", "")
                            if thought:
                                reasoning.append(thought)
                                yield ReasoningDelta(thought)
                    elif event.get("type") == "message_delta":
                        if event.get("delta", {}).get("stop_reason") == "max_tokens":
                            truncated = True
                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            fallback_text.append(block.text)
                    # The assembled message carries stop_reason too — without
                    # this, a max_tokens cut recovered via the fallback path
                    # (no stream events) would pass as a complete reply.
                    truncated = truncated or message.stop_reason == "max_tokens"
                elif isinstance(message, ResultMessage):
                    result = message
                    truncated = truncated or message.stop_reason == "max_tokens"
        finally:
            # Abandoned mid-turn (Ctrl-C closes us at a yield): tear the
            # bridge — and with it the CLI subprocess — down deterministically.
            events.close()

        if not saw_text_delta and fallback_text:
            # Partial deltas never arrived (SDK/CLI drift?) but the assembled
            # assistant message did — better to recover the turn than drop it.
            yield from _show(fence.feed("".join(fallback_text)))
        yield from _show(fence.finish())

        if result is None:
            raise RuntimeError(
                "Claude stream ended without a result — the Claude Code "
                "subprocess died mid-turn, so the partial answer was discarded. "
                f"{_AUTH_HINT}"
            )
        # error_max_turns just means the model wanted another turn our
        # max_turns=1 didn't grant — the reply we streamed is still the turn.
        if result.is_error and result.subtype != "error_max_turns":
            detail = result.result or "; ".join(result.errors or []) or result.subtype
            raise RuntimeError(f"Claude turn failed ({result.subtype}): {detail}\n{_AUTH_HINT}")

        text = "".join(emitted) or None
        tool_calls = [
            ToolCall(id=f"call_{uuid.uuid4().hex[:12]}", name=call["name"], arguments=call["arguments"])
            for call in fence.calls
        ]
        assistant_message: dict = {"role": "assistant", "content": text}
        if tool_calls:
            # Same OpenAI wire shape DMRBrain replays: arguments as a JSON string.
            assistant_message["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {"name": tool_call.name, "arguments": json.dumps(tool_call.arguments)},
                }
                for tool_call in tool_calls
            ]

        yield BrainResponse(
            message=assistant_message,
            text=text,
            tool_calls=tool_calls,
            reasoning="".join(reasoning).strip() or None,
            truncated=truncated,
            context_tokens=_usage_tokens(result.usage),
        )

    def _stream_query(self, prompt: str, options: ClaudeAgentOptions) -> Iterator[object]:
        """Relay the SDK's async query synchronously, message by message.

        The SDK is asyncio-native; Vegapunk is synchronous generators all the
        way down. A daemon worker thread runs the event loop and pumps messages
        into a queue; this generator drains it. Closing the generator cancels
        the async task, which shuts the CLI subprocess down through the SDK's
        own cleanup; the join timeout (with the daemon flag as backstop) keeps
        a wedged subprocess from hanging the REPL.
        """
        handoff: queue.Queue[tuple[str, object]] = queue.Queue()

        async def _pump() -> None:
            try:
                async for message in query(prompt=prompt, options=options):
                    handoff.put(("msg", message))
            except asyncio.CancelledError:
                raise  # teardown we initiated — nothing to report
            except BaseException as exc:  # noqa: BLE001 — relayed and re-raised below
                handoff.put(("err", exc))
            else:
                handoff.put(("end", None))

        # The loop and task exist before the worker starts, so the teardown in
        # the finally below can always reach them — there is no started-but-
        # not-yet-registered window for a Ctrl-C to slip through (a cancel
        # scheduled before the loop runs is simply the first thing it does).
        loop = asyncio.new_event_loop()
        task = loop.create_task(_pump())

        def _run() -> None:
            try:
                try:
                    loop.run_until_complete(task)
                finally:
                    loop.close()
            except asyncio.CancelledError:
                pass  # expected on teardown
            except BaseException as exc:  # noqa: BLE001 — the queue is the only channel out
                handoff.put(("err", exc))

        worker = threading.Thread(target=_run, name="claude-brain-query", daemon=True)
        worker.start()
        try:
            while True:
                kind, payload = handoff.get()
                if kind == "msg":
                    yield payload
                elif kind == "end":
                    return
                else:
                    raise self._wrap_error(payload) from payload
        finally:
            if not task.done():
                # The loop may finish (and close) between the check and the
                # call; that race just means there's nothing left to cancel.
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(task.cancel)
            worker.join(timeout=5.0)
            if worker.is_alive():
                # A silent no-op here would hide a live subprocess still
                # spending the user's subscription in the background.
                print(
                    style.paint(
                        "  [claude] the model turn didn't shut down within 5s — "
                        "its subprocess may still be running in the background.",
                        style.YELLOW,
                        sys.stderr,
                    ),
                    file=sys.stderr,
                )

    @staticmethod
    def _wrap_error(exc: BaseException) -> RuntimeError:
        if isinstance(exc, CLINotFoundError):
            return RuntimeError(
                "Claude Code CLI not found — claude-agent-sdk bundles it, so "
                "try reinstalling the package (pip install --force-reinstall "
                "claude-agent-sdk)."
            )
        return RuntimeError(f"Claude call failed: {exc}\n{_AUTH_HINT}")
