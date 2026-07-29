"""Tests for the REPL control flow — deterministic, no model/network/TTY.

We drive ``cli.main`` with a ``ScriptedPrompter`` (canned inputs) and a
``FakeBrain``-backed ``Session``, capturing stdout via capsys. Commands are
slash-prefixed (``/exit``, ``/new``, …) — there are no bare keyword commands.
A reply now auto-saves the conversation, so the sessions dir is redirected to a
tmp path and reply turns queue a second response for the auto-naming title call.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from test_loop import _force_color  # sibling test modules (tests/ is on sys.path)
from test_session import FakeBrain, _text

from vegapunk import db, style
from vegapunk.brain import TextDelta
from vegapunk.cli import main
from vegapunk.prompter import ScriptedPrompter
from vegapunk.session import Session


def _session(responses):
    return Session(FakeBrain(responses), tools=[], system_prompt="SYS")


def test_exit_quits(capsys):
    main(prompter=ScriptedPrompter(["/exit"]), session=_session([]))
    assert "bye." in capsys.readouterr().out


def test_quit_quits(capsys):
    main(prompter=ScriptedPrompter(["/quit"]), session=_session([]))
    assert "bye." in capsys.readouterr().out


def test_eof_prints_bye_and_returns(capsys):
    main(prompter=ScriptedPrompter([EOFError]), session=_session([]))
    assert "bye." in capsys.readouterr().out


def test_ctrl_c_at_prompt_continues_then_exits(capsys):
    main(prompter=ScriptedPrompter([KeyboardInterrupt, "/exit"]), session=_session([]))
    out = capsys.readouterr().out
    assert "interrupted — type /exit" in out  # proves the loop continued, didn't crash
    assert "bye." in out


def test_unknown_command_is_reported(capsys):
    main(prompter=ScriptedPrompter(["/nope", "/exit"]), session=_session([]))
    assert "Unknown command /nope" in capsys.readouterr().out


def test_empty_input_never_reaches_send(capsys):
    # FakeBrain has no queued responses, so if the empty turn called send()
    # the loop would blow up popping an empty list. Reaching "/exit" proves
    # empty input was skipped.
    main(prompter=ScriptedPrompter(["", "/exit"]), session=_session([]))
    assert "bye." in capsys.readouterr().out


def test_reply_is_printed_and_session_autosaved(capsys):
    # Queue: the turn reply, then the title for auto-naming.
    main(prompter=ScriptedPrompter(["hi", "/exit"]), session=_session([_text("yo"), _text("a chat")]))
    out = capsys.readouterr().out
    assert "vega> yo" in out
    assert "(saved as 'a-chat')" in out  # auto-named from the title call


def test_streamed_reply_is_not_printed_twice(capsys):
    # The reply arrives as streamed deltas AND as send()'s return value; only
    # the stream is rendered — the return must not be printed again.
    main(
        prompter=ScriptedPrompter(["hi", "/exit"]),
        session=_session([_text("unmistakable-reply"), _text("title")]),
    )
    out = capsys.readouterr().out
    assert out.count("unmistakable-reply") == 1
    assert "vega> unmistakable-reply\n" in out  # and the streamed line is closed


def test_empty_reply_still_gets_its_prompt_line(capsys):
    # A model turn with no text at all: the vega> line still appears (blank),
    # so the user sees the turn ended rather than a silently missing reply.
    main(
        prompter=ScriptedPrompter(["hi", "/exit"]),
        session=_session([_text(""), _text("title")]),
    )
    assert "vega> \n" in capsys.readouterr().out


def test_new_clears_history(capsys):
    session = _session([_text("hi there"), _text("greeting")])  # reply + title
    main(prompter=ScriptedPrompter(["hello", "/new", "/exit"]), session=session)
    out = capsys.readouterr().out
    assert "(new conversation)" in out
    assert session.messages == [{"role": "system", "content": "SYS"}]  # back to just the prompt


class _BoomSession:
    """A session whose send() raises at call time. A real (generator) send can't
    do that, but the CLI guards the events-not-yet-created window anyway — this
    pins that defensive branch."""

    brain = FakeBrain([])  # main() reads session.brain for the banner

    def send(self, _user_input: str) -> str:
        raise KeyboardInterrupt


def test_ctrl_c_before_the_stream_starts_is_caught(capsys):
    main(prompter=ScriptedPrompter(["go", "/exit"]), session=_BoomSession())
    out = capsys.readouterr().out
    assert "(interrupted)" in out  # caught the interrupt
    assert "bye." in out  # and the loop survived to handle the next input


class _MidStreamInterruptSession:
    """send() dies mid-stream the way the real Session does when Ctrl-C lands
    inside a pull: the generator raises after rolling its history back."""

    brain = FakeBrain([])  # main() reads session.brain for the banner

    def send(self, _user_input: str):
        def stream():
            yield TextDelta("par")
            raise KeyboardInterrupt

        return stream()


def test_ctrl_c_mid_generation_is_caught_after_partial_output(capsys):
    main(prompter=ScriptedPrompter(["go", "/exit"]), session=_MidStreamInterruptSession())
    out = capsys.readouterr().out
    assert "vega> par" in out  # the partial text had already streamed
    assert "(interrupted)" in out  # the interrupt was caught mid-line
    assert "bye." in out  # and the loop survived


class _FailingTurnSession:
    """send() streams a little then raises, like ClaudeBrain when the backend
    is unauthenticated or the subprocess dies — after rolling back, the real
    Session re-raises exactly like this."""

    brain = FakeBrain([])  # main() reads session.brain for the banner

    def send(self, _user_input: str):
        def stream():
            yield TextDelta("par")
            raise RuntimeError("Claude turn failed: run `claude /login` first")

        return stream()


def test_a_failed_turn_prints_the_error_and_the_repl_survives(capsys):
    main(prompter=ScriptedPrompter(["go", "/exit"]), session=_FailingTurnSession())
    out = capsys.readouterr().out
    assert "[error]" in out
    assert "claude /login" in out  # the actionable message reaches the user
    assert "bye." in out  # the REPL lived on — /model local remains possible


class _SuspendedReply:
    """Stands in for a send() stream when Ctrl-C lands *between* pulls (in CLI
    code, while the generator is suspended): the interrupt never reaches the
    generator, so the CLI's close() call is the only thing that triggers the
    session's rollback."""

    def __init__(self) -> None:
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        raise KeyboardInterrupt  # the interrupt surfaces from the CLI's pull

    def close(self) -> None:
        self.closed = True


class _SuspendedSession:
    brain = FakeBrain([])  # main() reads session.brain for the banner

    def __init__(self) -> None:
        self.reply = _SuspendedReply()

    def send(self, _user_input: str):
        return self.reply


def test_ctrl_c_between_pulls_closes_the_stream_for_deterministic_rollback(capsys):
    session = _SuspendedSession()
    main(prompter=ScriptedPrompter(["go", "/exit"]), session=session)
    out = capsys.readouterr().out
    assert "(interrupted)" in out
    assert session.reply.closed  # the CLI forced the rollback; GC timing never decides it


def test_autosave_failure_does_not_crash_repl(capsys, monkeypatch):
    # A disk error during the trailing autosave must degrade to a note, not tear
    # down the live conversation.
    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("vegapunk.session_store.save_session", boom)
    main(prompter=ScriptedPrompter(["hi", "/exit"]), session=_session([_text("yo"), _text("title")]))

    captured = capsys.readouterr()
    assert "vega> yo" in captured.out  # reply still shown
    assert "could not save" in captured.err  # degraded on stderr
    assert "bye." in captured.out  # survived to the next prompt and /exit


def test_banner_shows_model_and_workspace(capsys):
    main(prompter=ScriptedPrompter(["/exit"]), session=_session([]))
    out = capsys.readouterr().out
    assert "model " in out and "workspace " in out  # plain under capsys (not a TTY)


def test_vega_prefix_is_bold_magenta_when_forced(monkeypatch, capsys):
    _force_color(monkeypatch)
    main(prompter=ScriptedPrompter(["hi", "/exit"]), session=_session([_text("yo"), _text("t")]))
    out = capsys.readouterr().out
    # Prefix wrapped, reset before the space, reply text plain.
    assert f"{style.BOLD}{style.MAGENTA}vega>{style.RESET} yo" in out


def test_interrupted_note_is_yellow_when_forced(monkeypatch, capsys):
    _force_color(monkeypatch)
    main(prompter=ScriptedPrompter(["go", "/exit"]), session=_MidStreamInterruptSession())
    out = capsys.readouterr().out
    assert f"{style.YELLOW}(interrupted){style.RESET}" in out


class _LabeledBrain(FakeBrain):
    """A FakeBrain with a declared identity, like a real provider has."""

    def __init__(self, label: str, window: int = 0) -> None:
        super().__init__([])
        self._label, self._window = label, window

    @property
    def model_label(self) -> str:
        return self._label

    @property
    def context_window(self) -> int:
        return self._window


def test_status_line_shows_the_live_brains_model_and_session_name():
    from vegapunk.cli import _status_line
    from vegapunk.commands import CommandContext

    ctx = CommandContext(session=Session(_LabeledBrain("ai/test"), tools=[], system_prompt="SYS"))
    # rstrip: the line is padded to the terminal width for the right gauge.
    assert _status_line(ctx).rstrip() == " ai/test · unsaved"  # before the first autosave
    ctx.current_name = "my-chat"
    assert _status_line(ctx).rstrip() == " ai/test · my-chat"  # /save and autosave show live


def test_status_line_follows_a_model_switch():
    from vegapunk.cli import _status_line
    from vegapunk.commands import CommandContext

    ctx = CommandContext(session=Session(_LabeledBrain("ai/test"), tools=[], system_prompt="SYS"))
    ctx.session.swap_brain(_LabeledBrain("claude"))
    assert _status_line(ctx).rstrip() == " claude · unsaved"


def test_context_gauge_formats_absolute_and_percent():
    from vegapunk.cli import _context_gauge

    assert _context_gauge(None, 131072) == ""  # before the first turn: no gauge
    assert _context_gauge(13107, 131072) == "13,107/131,072 tok (10%) "

    # Unknown window (0): absolute only — never a % against a guessed size.
    assert _context_gauge(13107, 0) == "13,107 tok "


def test_gauge_uses_the_live_brains_window():
    from vegapunk.cli import _status_line
    from vegapunk.commands import CommandContext

    ctx = CommandContext(
        session=Session(_LabeledBrain("claude", window=200000), tools=[], system_prompt="SYS")
    )
    ctx.session.context_tokens = 20000
    assert _status_line(ctx).endswith("20,000/200,000 tok (10%) ")


def test_status_line_right_aligns_the_gauge_to_the_terminal(monkeypatch):
    import os

    from vegapunk.cli import _status_line
    from vegapunk.commands import CommandContext

    monkeypatch.setattr(
        "vegapunk.cli.shutil.get_terminal_size", lambda: os.terminal_size((80, 24))
    )
    ctx = CommandContext(
        session=Session(_LabeledBrain("ai/test", window=131072), tools=[], system_prompt="SYS")
    )
    ctx.session.context_tokens = 500
    line = _status_line(ctx)
    assert len(line) == 80  # padded so the gauge lands on the right edge
    assert line.endswith("tok (0%) ")
    assert "unsaved" in line


def test_main_builds_the_brain_from_the_configured_provider(monkeypatch, capsys):
    from vegapunk.config import config

    seen: dict = {}

    def fake_create(provider):
        seen["provider"] = provider
        return _LabeledBrain("stub-model")

    monkeypatch.setattr("vegapunk.cli.create_brain", fake_create)
    main(prompter=ScriptedPrompter(["/exit"]))  # session=None: built from config

    assert seen["provider"] == config.provider  # "local" unless VEGAPUNK_PROVIDER says otherwise
    assert "model stub-model" in capsys.readouterr().out  # banner shows the live brain


class _SpyPopen:
    """Stands in for the spawned worker process, capturing how cli drives it."""

    instances: list["_SpyPopen"] = []

    def __init__(self, argv, stdin=None, stdout=None, stderr=None, env=None):
        self.argv = argv
        self.env = env or {}
        self.stdout = stdout
        self.terminated = 0
        self.killed = 0
        self.waited = 0
        self.returncode = None  # still running until terminate()
        _SpyPopen.instances.append(self)

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated += 1
        self.returncode = 0

    def wait(self, timeout=None):
        self.waited += 1
        return self.returncode

    def kill(self) -> None:
        self.killed += 1


@pytest.fixture
def spy_worker(monkeypatch):
    _SpyPopen.instances = []
    monkeypatch.setattr("vegapunk.cli.subprocess.Popen", _SpyPopen)
    return _SpyPopen


def test_main_spawns_the_worker_as_a_separate_process(spy_worker, capsys):
    main(prompter=ScriptedPrompter(["/exit"]), session=_session([]))

    assert len(spy_worker.instances) == 1
    worker = spy_worker.instances[0]
    # Launched the same way the REPL itself is, so it shares the interpreter and
    # virtualenv rather than depending on a console script being installed.
    assert worker.argv == [sys.executable, "-m", "vegapunk.scheduler_worker"]
    # The database is named explicitly: parent and child must not have to agree
    # on a working directory to end up on the same file.
    assert worker.env["VEGAPUNK_DB_FILE"] == str(db.db_path())


def test_main_stops_the_worker_on_exit(spy_worker, capsys):
    main(prompter=ScriptedPrompter(["/exit"]), session=_session([]))
    worker = spy_worker.instances[0]
    assert worker.terminated == 1  # SIGTERM, so a task mid-run records its outcome
    assert worker.killed == 0


def test_main_stops_the_worker_on_eof(spy_worker, capsys):
    main(prompter=ScriptedPrompter([EOFError]), session=_session([]))
    assert spy_worker.instances[0].terminated == 1  # finally covers the Ctrl-D path too


def test_main_kills_a_worker_that_ignores_terminate(spy_worker, capsys, monkeypatch):
    # A worker inside a model call can't honor SIGTERM (the stop flag isn't read
    # again until the turn returns), and a turn routinely outlasts the grace
    # period — so it's killed rather than hanging /exit, and said out loud
    # because the run is genuinely lost.
    def _stubborn_wait(self, timeout=None):
        raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout or 0)

    monkeypatch.setattr(_SpyPopen, "wait", _stubborn_wait)
    main(prompter=ScriptedPrompter(["/exit"]), session=_session([]))
    assert spy_worker.instances[0].killed == 1
    assert "stays due" in capsys.readouterr().err


def test_main_reports_a_worker_that_died(spy_worker, capsys):
    # Otherwise scheduled tasks just silently stop happening. Reported once,
    # pointing at the log that says why.
    def _already_dead(self):
        self.returncode = 1
        return 1

    _SpyPopen.poll = _already_dead
    try:
        main(prompter=ScriptedPrompter(["", "/exit"]), session=_session([]))
    finally:
        del _SpyPopen.poll
    out = capsys.readouterr().out
    assert "worker exited (1)" in out
    assert "scheduler.log" in out
    assert out.count("worker exited") == 1  # said once, not on every prompt


def test_main_survives_a_worker_that_cannot_start(monkeypatch, capsys):
    # No scheduled tasks is a degraded session, not a dead one.
    def _boom(*a, **k):
        raise OSError("no fork for you")

    monkeypatch.setattr("vegapunk.cli.subprocess.Popen", _boom)
    main(prompter=ScriptedPrompter(["/exit"]), session=_session([]))
    assert "could not start the worker" in capsys.readouterr().err


@pytest.fixture
def skills_home(tmp_path, monkeypatch):
    monkeypatch.setattr("vegapunk.skills.skills_dir", lambda: tmp_path)
    return tmp_path


def test_staged_skill_rides_the_next_message_then_clears(skills_home, capsys):
    (skills_home / "commit-message").mkdir()
    (skills_home / "commit-message" / "SKILL.md").write_text(
        "---\ndescription: d\n---\nUse type(scope): summary.", encoding="utf-8"
    )
    brain = FakeBrain([_text("done"), _text("title"), _text("also done"), _text("title2")])
    session = Session(brain, tools=[], system_prompt="SYS")
    main(
        prompter=ScriptedPrompter(
            ["/skill commit-message", "/history", "write a commit message", "plain follow-up", "/exit"]
        ),
        session=session,
    )

    # The staging survived the intervening /history command, then rode the
    # first real message: skill body first, a closing marker, then the request.
    first_user = next(m["content"] for m in brain.seen_messages[0] if m["role"] == "user")
    assert first_user.startswith("[Skill 'commit-message' — follow these instructions")
    assert "Use type(scope): summary." in first_user
    assert "[End of skill instructions. The request:]" in first_user
    assert first_user.rstrip().endswith("write a commit message")

    # ...and was cleared afterwards: the follow-up message is unadorned.
    second_user = [m["content"] for m in brain.seen_messages[2] if m["role"] == "user"][-1]
    assert second_user == "plain follow-up"
