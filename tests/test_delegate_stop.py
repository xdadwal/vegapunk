"""Stopping a turn stops child inference, without abandoning host operations."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

from tests.fake_provider import FakeProvider, backend_for, call, says, wants
from vegapunk.config import config
from vegapunk.delegate_activity import DelegateActivity
from vegapunk.delegation import RoleRunner
import pytest


@pytest.fixture(autouse=True)
def _clear_global_runner():
    from vegapunk import delegation

    delegation.set_runner(None)
    yield
    delegation.set_runner(None)


class SlowProvider(FakeProvider):
    def __init__(self, started):
        super().__init__(says("must not finish normally", delay=2))
        self.started = started

    async def stream(self, request):
        self.started.set()
        async for event in super().stream(request):
            yield event


def test_stop_all_cancels_child_inference_and_records_cancelled(tmp_path):
    started = Event()
    activity = DelegateActivity()
    child = SlowProvider(started)
    backend = replace(backend_for(), spawn_provider=lambda: child)
    runner = RoleRunner(lambda: backend, [], replace(config, db_file=tmp_path / "db"),
                        activity=activity)
    with ThreadPoolExecutor(1) as pool:
        work = pool.submit(runner.run, "atlas", "inspect")
        assert started.wait(1)
        runner.stop_all()
        assert "cancelled" in work.result(timeout=0.5).lower()
    run, = activity.snapshot()
    assert run.status == "cancelled"
    assert run.ended is not None


def test_stop_all_blocks_late_roles_until_next_turn(tmp_path):
    spawned = []

    def backend():
        spawned.append(True)
        return backend_for()

    runner = RoleRunner(backend, [], replace(config, db_file=tmp_path / "db"))
    runner.stop_all()
    assert "cancelled" in runner.run("atlas", "late sibling").lower()
    assert spawned == []
    runner.prepare_turn()
    assert "scripted title" in runner.run("atlas", "next turn")
    assert spawned == [True]


def test_stop_all_cancels_concurrent_roles(tmp_path):
    started = [Event(), Event()]
    children = [SlowProvider(event) for event in started]
    from threading import Lock
    lock = Lock()

    def spawn():
        with lock:
            return children.pop(0)

    backend = replace(backend_for(), spawn_provider=spawn)
    runner = RoleRunner(lambda: backend, [], replace(config, db_file=tmp_path / "db"))
    with ThreadPoolExecutor(2) as pool:
        work = [pool.submit(runner.run, role, "inspect") for role in ("atlas", "shaka")]
        assert all(event.wait(1) for event in started)
        runner.stop_all()
        assert all("cancelled" in future.result(timeout=0.5).lower() for future in work)


def test_stop_all_drains_inflight_sync_handler(tmp_path):
    from logpose import tool

    started, release, finished = Event(), Event(), Event()

    @tool
    def operation() -> str:
        """Finish a started operation before the test returns."""
        started.set()
        assert release.wait(2)
        finished.set()
        return "finished"

    child = FakeProvider([wants(call("operation")), says("done")])
    backend = replace(backend_for(), spawn_provider=lambda: child)
    runner = RoleRunner(lambda: backend, [operation], replace(config, db_file=tmp_path / "db"))
    with ThreadPoolExecutor(1) as pool:
        work = pool.submit(runner.run, "atlas", "operate")
        assert started.wait(1)
        try:
            runner.stop_all()
            assert not work.done()
        finally:
            release.set()
        assert "cancelled" in work.result(timeout=1).lower()
    assert finished.is_set()


def test_session_cancel_stops_children_and_next_turn_is_usable(tmp_path):
    from vegapunk import delegation
    from vegapunk.cli import _render_reply
    from vegapunk.interactive_agent import TurnCancelled
    from vegapunk.session import Session
    from vegapunk.tools.delegation import delegate

    started = Event()
    child = SlowProvider(started)
    backend = replace(backend_for([
        wants(call("delegate", {"role": "atlas", "task": "inspect"})), says("next turn works"),
    ]), spawn_provider=lambda: child)
    activity = DelegateActivity()
    runner = RoleRunner(lambda: backend, [], replace(config, db_file=tmp_path / "db"),
                        activity=activity)
    delegation.set_runner(runner)
    session = Session(backend, [delegate])
    try:
        with ThreadPoolExecutor(1) as pool:
            work = pool.submit(_render_reply, session.send("inspect"), session.renderer)
            assert started.wait(1)
            session.cancel()
            with pytest.raises(TurnCancelled):
                work.result(timeout=0.5)
        assert activity.snapshot()[0].status == "cancelled"
        assert session.messages == []
        _render_reply(session.send("continue"), session.renderer)
        assert "next turn works" in repr(session.messages)
    finally:
        session.close()


def test_manual_stop_option_stops_only_requesting_child(monkeypatch, tmp_path):
    from vegapunk.approval import CLIApprover
    from vegapunk.tools import ALL_TOOLS

    monkeypatch.setattr("vegapunk.approval.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("vegapunk.tools.workspace.config", replace(config, workspace_root=str(tmp_path)))
    approver = CLIApprover()
    monkeypatch.setattr(approver, "_ask", lambda *args: "stop")
    child = FakeProvider(wants(call("write_file", {"path": "must-not-write", "content": "no"})))
    backend = replace(backend_for(), spawn_provider=lambda: child)
    activity = DelegateActivity()
    runner = RoleRunner(lambda: backend, ALL_TOOLS, replace(config, db_file=tmp_path / "db"),
                        approver=approver, activity=activity)
    assert "cancelled" in runner.run("lilith", "write").lower()
    assert activity.snapshot()[0].status == "cancelled"
    assert child.call_count == 1
    assert not (tmp_path / "must-not-write").exists()


def test_subagent_stats_have_their_own_row_and_visible_stop_hint():
    import io
    from rich.console import Console
    from vegapunk import terminal
    from vegapunk.delegate_activity import DelegateDisplay

    output = io.StringIO()
    activity = DelegateActivity()
    display = DelegateDisplay(activity, Console(file=output, force_terminal=True, width=140),
                              session_status=lambda: "auto · primary-model · chat · 100 tok")
    key = activity.start("Atlas", "model")
    try:
        with terminal.LOCK:
            display._draw()
            display.pause()
        assert "Sub-agents:" in output.getvalue()
        assert "Ctrl-C stop all" in output.getvalue()
        assert "Session: auto · primary-model · chat · 100 tok" in output.getvalue()
    finally:
        activity.finish(key, "cancelled")
        display.close()


def test_real_sigint_drains_children_before_next_sequential_turn(tmp_path):
    import os
    import signal
    from threading import Thread
    from vegapunk import delegation
    from vegapunk.cli import _render_reply
    from vegapunk.session import Session
    from vegapunk.tools.delegation import delegate

    started = Event()
    child = SlowProvider(started)
    backend = replace(backend_for([
        wants(call("delegate", {"role": "atlas", "task": "inspect"})), says("next turn works"),
    ]), spawn_provider=lambda: child)
    activity = DelegateActivity()
    runner = RoleRunner(lambda: backend, [], replace(config, db_file=tmp_path / "db"),
                        activity=activity)
    delegation.set_runner(runner)
    session = Session(backend, [delegate])

    def interrupt():
        if started.wait(2):
            os.kill(os.getpid(), signal.SIGINT)

    interrupter = Thread(target=interrupt)
    interrupter.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            _render_reply(session.send("inspect"), session.renderer)
        assert activity.snapshot()[0].status == "cancelled"
        assert activity.snapshot()[0].ended is not None
        # The first next() must succeed, without waiting/retrying a still-busy runner.
        _render_reply(session.send("continue"), session.renderer)
        assert "next turn works" in repr(session.messages)
    finally:
        interrupter.join(timeout=2)
        session.close()


def test_repeated_sigint_does_not_abandon_running_child_operation(tmp_path):
    import os
    import signal
    import time
    from threading import Thread
    from logpose import tool
    from vegapunk import delegation
    from vegapunk.cli import _render_reply
    from vegapunk.session import Session
    from vegapunk.tools.delegation import delegate

    started, release, finished = Event(), Event(), Event()

    @tool
    def operation() -> str:
        """Complete before control can return to the next user turn."""
        started.set()
        assert release.wait(2)
        finished.set()
        return "finished"

    child = FakeProvider([wants(call("operation")), says("late")])
    backend = replace(backend_for([
        wants(call("delegate", {"role": "atlas", "task": "operate"})), says("next turn works"),
    ]), spawn_provider=lambda: child)
    activity = DelegateActivity()
    runner = RoleRunner(lambda: backend, [operation], replace(config, db_file=tmp_path / "db"),
                        activity=activity)
    delegation.set_runner(runner)
    session = Session(backend, [delegate])

    def interrupt():
        if started.wait(2):
            try:
                os.kill(os.getpid(), signal.SIGINT)
                time.sleep(0.02)
                os.kill(os.getpid(), signal.SIGINT)
                time.sleep(0.08)
            finally:
                release.set()

    interrupter = Thread(target=interrupt)
    interrupter.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            _render_reply(session.send("operate"), session.renderer)
        assert finished.is_set()
        assert activity.snapshot()[0].status == "cancelled"
        _render_reply(session.send("continue"), session.renderer)
        assert "next turn works" in repr(session.messages)
    finally:
        release.set()
        interrupter.join(timeout=2)
        session.close()


def test_original_prompter_has_no_busy_composer_or_queue():
    from vegapunk.prompter import PromptToolkitPrompter

    assert not hasattr(PromptToolkitPrompter, "run_turn")
    assert not hasattr(PromptToolkitPrompter, "queued")


def test_stopping_one_child_leaves_sibling_approval_and_work_available(monkeypatch, tmp_path):
    import asyncio
    from threading import local
    from vegapunk.approval import CLIApprover
    from vegapunk.tools import ALL_TOOLS

    cfg = replace(config, db_file=tmp_path / "db", workspace_root=str(tmp_path))
    monkeypatch.setattr("vegapunk.tools.workspace.config", cfg)
    monkeypatch.setattr("vegapunk.approval.sys.stdin.isatty", lambda: True)
    approver = CLIApprover()
    monkeypatch.setattr(approver, "_ask", lambda name, args:
                        "stop" if args["path"] == "cancelled.txt" else "yes")
    started, release = Event(), Event()

    class Sibling(FakeProvider):
        async def stream(self, request):
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            async for event in super().stream(request):
                yield event

    children = {
        "lilith": FakeProvider(wants(call("write_file", {"path": "cancelled.txt", "content": "no"}))),
        "atlas": Sibling([wants(call("write_file", {"path": "sibling.txt", "content": "yes"})),
                           says("sibling completed")]),
    }
    selected = local()
    backend = replace(backend_for(), spawn_provider=lambda: children[selected.role])
    runner = RoleRunner(lambda: backend, ALL_TOOLS, cfg, approver=approver)

    def run(role):
        selected.role = role
        return runner.run(role, "write")

    with ThreadPoolExecutor(2) as pool:
        cancelled, sibling = pool.submit(run, "lilith"), pool.submit(run, "atlas")
        try:
            assert started.wait(1)
            assert "cancelled" in cancelled.result(timeout=1).lower()
            assert not sibling.done()
        finally:
            release.set()
        assert "sibling completed" in sibling.result(timeout=1)
    assert not (tmp_path / "cancelled.txt").exists()
    assert (tmp_path / "sibling.txt").read_text() == "yes"
