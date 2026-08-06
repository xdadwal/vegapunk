"""Tests for the loop's live trace and streaming contract — no model, no network.

logpose runs the loop; this file pins the part Vegapunk kept — turning its event
stream into something a human can watch, without ever letting that watching
leak into the answer.

Two channels, tested separately. Reply text is re-yielded upward for the CLI to
render, and anything returned was yielded as deltas first (the display
invariant), so a renderer never has to decide whether the return value still
needs printing. Everything else — steps, reasoning, tool results, the spinner —
goes to stderr and is asserted through capsys.

Mostly driven by a scripted provider (``tests/fake_provider``), so what the
"model" says on each round trip is exact — the trace is largely *about* what the
loop did, so a real agent over a fake provider is the honest fixture. Where a
contract is pure rendering, ``trace`` is handed hand-written events instead; it
takes an event stream and nothing else, so no agent is required to test it.
"""

from __future__ import annotations

from dataclasses import replace
from unittest import mock

import pytest
from logpose import (
    Conversation,
    RunEnd,
    RunResult,
    TextDelta,
    ThinkingDelta,
    TurnEnd,
    Usage,
    stream_sync,
)

from logpose import tool

from vegapunk import style
from vegapunk.approval import Decision, ScriptedApprover
from vegapunk.gate import DENIED, NO_GATE
from vegapunk.loop import STEP_LIMIT_NOTICE, run, trace
from vegapunk.render import PlainRenderer
from tests.fake_provider import Turn, agent_for, call, says, wants


def _force_color(monkeypatch) -> None:
    """Turn color on regardless of capsys's non-TTY streams (mode 'always')."""
    monkeypatch.setattr("vegapunk.style.config", replace(style.config, color="always"))


# ---------------------------------------------------------------------------
# tools used by the tests
# ---------------------------------------------------------------------------


@tool
def echo(text: str) -> str:
    """Echo some text.

    Args:
        text: What to echo.
    """
    return text


@tool
def boom() -> str:
    """Always fails."""
    raise RuntimeError("kaboom")


@tool
def long_output() -> str:
    """Return more text than the trace shows."""
    return "x" * 500


@tool
def touch(path: str) -> str:
    """Pretend to write a file.

    Args:
        path: Where to write.
    """
    return f"wrote {path}"


TOOLS = [echo, boom, long_output, touch]


@pytest.fixture(autouse=True)
def _guarded_names(monkeypatch):
    """Guard exactly the tools these tests treat as side-effecting.

    Patched rather than registered: the real GUARDED is populated at import
    time from the real tool set, and a test tool has no business in it.
    """
    monkeypatch.setattr("vegapunk.gate.GUARDED", {"touch", "slow_touch"})


def _drive(turns, renderer=None, **kwargs) -> tuple[list[TextDelta], str, int | None]:
    """Run one request and split what it yielded from what it returned."""
    kwargs.setdefault("tools", TOOLS)
    agent, _provider = agent_for(turns, **kwargs)
    generator = trace(
        stream_sync(agent, "go", conversation=Conversation()), renderer or PlainRenderer()
    )
    deltas: list[TextDelta] = []
    while True:
        try:
            deltas.append(next(generator))
        except StopIteration as stop:
            reply, context_tokens = stop.value
            return deltas, reply, context_tokens


# ---------------------------------------------------------------------------
# 1. the streaming contract
# ---------------------------------------------------------------------------


def test_text_deltas_are_re_yielded_and_the_reply_returned():
    deltas, reply, _ = _drive(says("all done", chunks=("all ", "done")))

    assert [d.text for d in deltas] == ["all ", "done"]
    assert reply == "all done"


def test_a_provider_that_did_not_stream_still_gets_its_answer_displayed():
    # The display invariant: a renderer prints exactly what it receives, so an
    # answer that arrived only in the final message has to be synthesized into
    # a delta rather than appearing solely in the return value.
    deltas, reply, _ = _drive(says("assembled, not streamed", stream_text=False))

    assert [d.text for d in deltas] == ["assembled, not streamed"]
    assert reply == "assembled, not streamed"


def test_speaking_before_a_tool_call_closes_the_spoken_line():
    # A Mock wrapping a real PlainRenderer: calls still behave like the real
    # renderer (so tool_call/tool_result etc. work as usual), but are also
    # recorded, so the test can assert reply_break actually fired instead of
    # only inferring it from a deltas list that no longer carries it.
    renderer = mock.Mock(wraps=PlainRenderer())
    deltas, _reply, _ = _drive(
        [wants(call("echo", {"text": "hi"}), text="Let me check."), says("done")],
        renderer=renderer,
    )

    # No synthetic "\n" delta: the line break is now told to the renderer
    # directly (reply_break), not faked as if the model had said it.
    assert [d.text for d in deltas] == ["Let me check.", "done"]
    calls = [c[0] for c in renderer.method_calls]
    assert "reply_break" in calls
    assert calls.index("reply_break") < calls.index("tool_call")


def test_a_tool_only_turn_yields_no_stray_newline():
    deltas, _reply, _ = _drive([wants(call("echo", {"text": "hi"})), says("done")])

    assert [d.text for d in deltas] == ["done"]


def test_reasoning_is_traced_to_stderr_and_never_yielded(capsys):
    deltas, reply, _ = _drive(says("the answer", thinking="pondering"))

    assert [d.text for d in deltas] == ["the answer"]  # reasoning is not re-yielded
    err = capsys.readouterr().err
    assert "[reason] pondering" in err
    assert "pondering" not in reply


def test_the_reasoning_line_closes_when_the_reply_starts(capsys):
    _drive(says("answer", thinking="thought"))

    err = capsys.readouterr().err
    # One [reason] line, opened after the step marker and closed before the
    # reply — so the trace reads a line per turn even though it's written live.
    assert err.count("[reason]") == 1
    assert err.index("[think] step 1") < err.index("[reason] thought")
    assert "thought\n" in err


def test_the_reasoning_line_closes_on_a_tool_only_turn(capsys):
    _drive([wants(call("echo", {"text": "hi"}), thinking="deciding"), says("done")])

    err = capsys.readouterr().err
    # The tool trace must start on its own line, not glued to the reasoning.
    assert "deciding\n" in err
    assert "\n  [tool] echo" in err


# ---------------------------------------------------------------------------
# 2. the step trace
# ---------------------------------------------------------------------------


def test_one_step_marker_per_model_round_trip(capsys):
    _drive([wants(call("echo", {"text": "a"})), wants(call("echo", {"text": "b"})), says("done")])

    err = capsys.readouterr().err
    # Three provider calls, three markers — tool events belong to the step that
    # asked for them, not to a step of their own.
    assert err.count("[think] step") == 3
    assert "[think] step 3" in err
    assert "[think] step 4" not in err


def test_batched_tool_calls_share_one_step(capsys):
    _drive([wants(call("echo", {"text": "a"}), call("echo", {"text": "b"})), says("done")])

    err = capsys.readouterr().err
    # The distinction the trace exists to show: batched (one step, two tools)
    # versus chained (a step each).
    assert err.count("[think] step") == 2
    assert err.count("[tool] echo") == 2


def test_the_tool_trace_shows_the_arguments_and_the_result(capsys):
    _drive([wants(call("echo", {"text": "hello"})), says("done")])

    assert "[tool] echo({'text': 'hello'}) -> hello" in capsys.readouterr().err


def test_a_long_tool_result_is_truncated_in_the_trace_but_not_in_history(capsys):
    _drive([wants(call("long_output")), says("done")])
    err = capsys.readouterr().err

    assert "… (+300 more chars)" in err
    assert "x" * 500 not in err  # the trace is shortened...

    agent, provider = agent_for([wants(call("long_output")), says("done")], tools=TOOLS)
    run(agent, "go")
    sent = provider.requests[1].messages[-1].content[0].content
    assert sent == "x" * 500  # ...while the model still gets all of it


def test_a_failing_tool_is_reported_in_red_and_the_run_continues(capsys, monkeypatch):
    _force_color(monkeypatch)

    _, reply, _ = _drive([wants(call("boom")), says("recovered")])

    assert style.RED in capsys.readouterr().err
    assert reply == "recovered"  # a tool failure is a step, not the end


def test_a_successful_tool_is_reported_in_cyan(capsys, monkeypatch):
    _force_color(monkeypatch)

    _drive([wants(call("echo", {"text": "hi"})), says("done")])

    assert style.CYAN in capsys.readouterr().err


def test_an_unknown_tool_name_is_reported_as_a_failure(capsys, monkeypatch):
    _force_color(monkeypatch)

    _, reply, _ = _drive([wants(call("invented")), says("recovered")])

    err = capsys.readouterr().err
    assert style.RED in err
    assert "invented" in err
    # The model is told which tools do exist, so it can correct itself.
    assert "echo" in err


def test_a_truncated_turn_is_noted_on_the_watch_channel(capsys):
    _drive(says("cut off mid-", stop_reason="max_tokens"))

    assert "ran out of tokens" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 3. approval, through the gate
# ---------------------------------------------------------------------------


def test_a_guarded_tool_needs_approval_and_runs_when_allowed(capsys):
    _, reply, _ = _drive(
        [wants(call("touch", {"path": "f.txt"})), says("done")],
        approver=ScriptedApprover(default=True),
    )

    assert "[tool] touch({'path': 'f.txt'}) -> wrote f.txt" in capsys.readouterr().err
    assert reply == "done"


def test_a_declined_tool_is_blocked_and_the_model_told_not_to_retry():
    agent, provider = agent_for(
        [wants(call("touch", {"path": "f.txt"})), says("ok, I won't")],
        tools=TOOLS,
        approver=ScriptedApprover(default=False),
    )

    run(agent, "go")

    result = provider.requests[1].messages[-1].content[0]
    assert result.content == DENIED
    assert result.is_error is True


def test_declining_with_feedback_redirects_rather_than_failing(capsys, monkeypatch):
    _force_color(monkeypatch)
    agent, provider = agent_for(
        [wants(call("touch", {"path": "f.txt"})), says("doing that instead")],
        tools=TOOLS,
        approver=ScriptedApprover(
            decisions={"touch": Decision(allow=False, feedback="write to g.txt instead")}
        ),
    )

    run(agent, "go")

    result = provider.requests[1].messages[-1].content[0]
    assert "write to g.txt instead" in result.content
    # A steer is not a failure: it must not be reported as one to the model...
    assert result.is_error is False
    # ...nor painted like one in the trace.
    assert style.RED not in capsys.readouterr().err


def test_without_an_approver_a_guarded_tool_is_blocked_rather_than_run():
    # Fail-closed: this is what the scheduler worker relies on.
    agent, provider = agent_for(
        [wants(call("touch", {"path": "f.txt"})), says("understood")],
        tools=TOOLS,
        approver=None,
    )

    run(agent, "go")

    assert provider.requests[1].messages[-1].content[0].content == NO_GATE


def test_an_unguarded_tool_never_reaches_the_approver():
    approver = ScriptedApprover()

    _, reply, _ = _drive([wants(call("echo", {"text": "hi"})), says("done")], approver=approver)

    assert reply == "done"
    assert approver.calls == []  # read-only tools are never worth interrupting for


def test_every_guarded_call_is_approved_before_any_of_them_runs():
    # The order the model asked in is the order the human is asked, and no tool
    # starts while a decision on a sibling is still pending — an approver that
    # prompts on stdin can only be asked one thing at a time.
    events: list[str] = []

    class Recording(ScriptedApprover):
        def approve(self, tool_name, arguments):
            events.append(f"ask:{arguments['path']}")
            return super().approve(tool_name, arguments)

    @tool
    def slow_touch(path: str) -> str:
        """Pretend to write a file.

        Args:
            path: Where to write.
        """
        events.append(f"run:{path}")
        return f"wrote {path}"

    agent, _provider = agent_for(
        [wants(call("slow_touch", {"path": "a"}), call("slow_touch", {"path": "b"})), says("done")],
        tools=[slow_touch],
        approver=Recording(default=True),
    )

    run(agent, "go")

    assert events[:2] == ["ask:a", "ask:b"]
    assert sorted(events[2:]) == ["run:a", "run:b"]


def test_a_mixed_batch_keeps_every_result_paired_with_its_call():
    # Denied, free-running, and approved calls in one turn: if the results came
    # back out of order the model would read each tool's output as another's.
    agent, provider = agent_for(
        [
            wants(
                call("touch", {"path": "a"}, id="t1"),
                call("echo", {"text": "middle"}, id="t2"),
                call("touch", {"path": "b"}, id="t3"),
            ),
            says("done"),
        ],
        tools=TOOLS,
        approver=ScriptedApprover(default=False),
    )

    run(agent, "go")

    results = provider.requests[1].messages[-1].content
    assert [b.tool_use_id for b in results] == ["t1", "t2", "t3"]
    assert [b.content for b in results] == [DENIED, "middle", DENIED]


def test_no_spinner_is_running_while_the_human_is_being_asked(monkeypatch):
    # The wait between a ToolCall and its result is a tool running — or a human
    # being asked to approve it. A spinner there would animate on top of the
    # approval prompt.
    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    spinning = {"now": False}
    monkeypatch.setattr("vegapunk.loop._Spinner.start", lambda self: spinning.update(now=True))
    monkeypatch.setattr("vegapunk.loop._Spinner.stop", lambda self: spinning.update(now=False))
    observed: list[bool] = []

    class _Watching(ScriptedApprover):
        def approve(self, tool_name, arguments):
            observed.append(spinning["now"])
            return super().approve(tool_name, arguments)

    agent, _provider = agent_for(
        [wants(call("touch", {"path": "a"})), says("done")],
        tools=TOOLS,
        approver=_Watching(default=True),
    )

    run(agent, "go")

    assert observed == [False]


# ---------------------------------------------------------------------------
# 4. limits, footprint, and interruption
# ---------------------------------------------------------------------------


def test_hitting_the_step_limit_is_yielded_before_it_is_returned():
    deltas, reply, _ = _drive(
        wants(call("echo", {"text": "again"})), max_iterations=2, repeat_last=True
    )

    assert reply == STEP_LIMIT_NOTICE
    assert [d.text for d in deltas] == [STEP_LIMIT_NOTICE]  # the display invariant


def test_the_context_footprint_is_the_latest_turns_report():
    _, _, context_tokens = _drive(
        [
            wants(call("echo", {"text": "a"}), usage=Usage(input_tokens=10, output_tokens=5)),
            says("done", usage=Usage(input_tokens=40, output_tokens=7)),
        ]
    )

    # Everything the model read plus everything it wrote, from the last call —
    # what the next request would carry.
    assert context_tokens == 47


def test_a_turn_without_usage_does_not_wipe_the_previous_footprint():
    _, _, context_tokens = _drive(
        [
            wants(call("echo", {"text": "a"}), usage=Usage(input_tokens=30, output_tokens=2)),
            says("done"),  # no usage reported
        ]
    )

    assert context_tokens == 32


def test_cached_tokens_count_toward_the_footprint():
    # They still occupy the window even though they were cheap to send.
    _, _, context_tokens = _drive(
        says("done", usage=Usage(input_tokens=1, cache_read_input_tokens=100, output_tokens=2))
    )

    assert context_tokens == 103


def test_abandoning_the_stream_closes_the_provider():
    agent, provider = agent_for(
        [wants(call("echo", {"text": "a"})), says("done")], tools=TOOLS, repeat_last=True
    )
    generator = trace(stream_sync(agent, "go", conversation=Conversation()), PlainRenderer())
    next(generator)  # start the run, land on the first delta

    generator.close()

    # Walking away has to tear the request down, not leak it.
    assert provider.closed >= 1


def test_trace_renders_a_stream_it_did_not_create():
    """``trace`` is a function of the event stream and nothing else.

    Driven here with hand-written events and no agent, provider, or model in
    sight — which is the point of the signature. A regression that reaches back
    for the ``Agent`` (to peek at tools, or to start the run itself) fails here
    rather than quietly re-coupling the renderer to the loop.
    """
    closed = False

    def scripted():
        nonlocal closed
        try:
            yield ThinkingDelta("weighing it up")
            yield TextDelta("the answer")
            yield TurnEnd(stop_reason="end_turn", usage=Usage(input_tokens=7, output_tokens=3))
            yield RunEnd(result=RunResult(text="the answer"))
        finally:
            closed = True

    generator = trace(scripted(), PlainRenderer())
    deltas = []
    while True:
        try:
            deltas.append(next(generator))
        except StopIteration as stop:
            reply, context_tokens = stop.value
            break

    assert [d.text for d in deltas] == ["the answer"]
    assert reply == "the answer"
    assert context_tokens == 10  # input + output, per _footprint
    assert closed, "trace owns the stream it is handed and must close it"


def test_a_provider_failure_propagates_rather_than_being_swallowed():
    # A broken model is not a tool error to feed back — the turn is over.
    with pytest.raises(RuntimeError, match="provider exploded"):
        _drive(Turn(error=RuntimeError("provider exploded")))


# ---------------------------------------------------------------------------
# 5. the spinner
# ---------------------------------------------------------------------------


def test_the_spinner_draws_and_erases_on_a_tty(capsys, monkeypatch):
    from vegapunk.loop import _Spinner

    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    spinner = _Spinner()

    spinner.start()
    spinner.stop()

    err = capsys.readouterr().err
    assert "thinking…" in err
    # Erased on the way out, so whatever prints next starts on a clean line.
    assert err.endswith("\r\x1b[K")


def test_the_spinner_is_silent_off_a_tty(capsys, monkeypatch):
    from vegapunk.loop import _Spinner

    monkeypatch.setattr("sys.stderr.isatty", lambda: False)
    spinner = _Spinner()

    spinner.start()
    spinner.stop()

    assert capsys.readouterr().err == ""


def test_stopping_a_spinner_twice_is_harmless(monkeypatch):
    from vegapunk.loop import _Spinner

    monkeypatch.setattr("sys.stderr.isatty", lambda: True)
    spinner = _Spinner()
    spinner.start()

    spinner.stop()
    spinner.stop()  # the classic double-Ctrl-C


# ---------------------------------------------------------------------------
# 6. the renderer seam
# ---------------------------------------------------------------------------


def test_trace_prints_through_the_renderer_it_is_given():
    """The seam is real: nothing reaches a stream except through the renderer.

    Driven with a recording double rather than capsys, so a regression that
    re-adds a bare `print` to loop.py fails here instead of passing silently
    because the bytes happened to match.
    """

    class Recorder:
        def __init__(self):
            self.calls = []

        def __getattr__(self, name):
            def record(*args, **kwargs):
                self.calls.append((name, args, kwargs))

            return record

    recorder = Recorder()
    agent, _provider = agent_for(
        [wants(call("echo", {"text": "hi"}), thinking="mulling"), says("done")], tools=TOOLS
    )
    generator = trace(stream_sync(agent, "go", conversation=Conversation()), recorder)
    while True:
        try:
            next(generator)
        except StopIteration:
            break

    named = [name for name, _args, _kwargs in recorder.calls]
    # The gap between these two is where the tool runs and where a human may be
    # at an approval prompt, so a renderer must be told before it, not only after.
    assert named.index("tool_call") < named.index("tool_result")
    requested = next(c for c in recorder.calls if c[0] == "tool_call")
    assert requested[1] == ("echo", {"text": "hi"})
    assert named.count("step") == 2
    assert "reasoning" in named
    assert "reasoning_end" in named
    assert "tool_result" in named
    tool = next(c for c in recorder.calls if c[0] == "tool_result")
    assert tool[1][0] == "echo"           # name
    assert tool[1][1] == {"text": "hi"}   # arguments
    assert tool[1][2] == "hi"             # content
    assert tool[1][3] is False            # is_error
