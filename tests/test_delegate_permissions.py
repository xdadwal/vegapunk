"""Delegates use the primary approval policy, including real guarded handlers."""

from dataclasses import replace

import pytest

from tests.fake_provider import FakeProvider, backend_for, call, says, wants
from vegapunk.approval import ApprovalPolicy, CLIApprover, ScriptedApprover
from vegapunk.config import config
from vegapunk.delegation import RoleRunner
from vegapunk.tools import ALL_TOOLS


@pytest.mark.parametrize("allowed", [True, False])
def test_delegate_file_write_follows_shared_manual_decision(monkeypatch, tmp_path, allowed):
    cfg = replace(config, workspace_root=str(tmp_path))
    monkeypatch.setattr("vegapunk.tools.workspace.config", cfg)
    child = FakeProvider([
        wants(call("write_file", {"path": "evidence.txt", "content": "verified"})),
        says("finished"),
    ])
    backend = replace(backend_for(), spawn_provider=lambda: child)
    approver = ScriptedApprover(default=allowed)
    runner = RoleRunner(lambda: backend, ALL_TOOLS, cfg, approver=approver)

    runner.run("lilith", "write evidence.txt")

    assert (tmp_path / "evidence.txt").exists() is allowed
    assert approver.calls == [("write_file", {"path": "evidence.txt", "content": "verified"})]


def test_delegate_auto_shell_runs_without_terminal(monkeypatch, tmp_path):
    cfg = replace(config, workspace_root=str(tmp_path))
    monkeypatch.setattr("vegapunk.tools.workspace.config", cfg)
    child = FakeProvider([
        wants(call("run_shell", {"command": "pwd"})),
        says("checked"),
    ])
    backend = replace(backend_for(), spawn_provider=lambda: child)
    approver = CLIApprover(ApprovalPolicy(auto=True))
    runner = RoleRunner(lambda: backend, ALL_TOOLS, cfg, approver=approver,
                        approval_mode=lambda: approver.policy.mode)

    runner.run("atlas", "check working directory")

    assert str(tmp_path) in repr(child.requests[1].messages)
    assert "Approval mode: auto" in child.requests[0].system


def test_delegate_manual_without_terminal_denies(monkeypatch, tmp_path):
    monkeypatch.setattr("vegapunk.approval.sys.stdin.isatty", lambda: False)
    cfg = replace(config, workspace_root=str(tmp_path))
    monkeypatch.setattr("vegapunk.tools.workspace.config", cfg)
    child = FakeProvider([
        wants(call("write_file", {"path": "blocked.txt", "content": "no"})), says("denied"),
    ])
    backend = replace(backend_for(), spawn_provider=lambda: child)
    RoleRunner(lambda: backend, ALL_TOOLS, cfg, approver=CLIApprover()).run("lilith", "write")
    assert not (tmp_path / "blocked.txt").exists()


def test_live_policy_toggle_applies_to_next_delegate_action(monkeypatch, tmp_path):
    cfg = replace(config, workspace_root=str(tmp_path))
    monkeypatch.setattr("vegapunk.tools.workspace.config", cfg)
    monkeypatch.setattr("vegapunk.approval.sys.stdin.isatty", lambda: False)
    policy = ApprovalPolicy(auto=True)
    approver = CLIApprover(policy)
    children = []

    def spawn():
        child = FakeProvider([
            wants(call("write_file", {"path": f"{len(children)}.txt", "content": "yes"})),
            says("done"),
        ])
        children.append(child)
        return child

    backend = replace(backend_for(), spawn_provider=spawn)
    runner = RoleRunner(lambda: backend, ALL_TOOLS, cfg, approver=approver,
                        approval_mode=lambda: policy.mode)
    runner.run("lilith", "write")
    policy.toggle()
    runner.run("lilith", "write")
    assert (tmp_path / "0.txt").exists()
    assert not (tmp_path / "1.txt").exists()
    assert "Approval mode: manual" in children[1].requests[0].system


def test_real_cli_auto_wires_shared_approver_to_delegate(monkeypatch, tmp_path):
    from vegapunk import cli
    from vegapunk.prompter import ScriptedPrompter

    cfg = replace(config, workspace_root=str(tmp_path))
    monkeypatch.setattr("vegapunk.tools.workspace.config", cfg)
    child = FakeProvider([
        wants(call("write_file", {"path": "cli.txt", "content": "approved"})),
        says("written"),
    ])
    backend = replace(backend_for([
        wants(call("delegate", {"role": "lilith", "task": "write cli.txt"})), says("done"),
    ]), spawn_provider=lambda: child)
    monkeypatch.setattr(cli, "create_backend", lambda provider: backend)
    monkeypatch.setattr(cli, "_autosave_turn", lambda ctx: None)

    cli.main(prompter=ScriptedPrompter(["write", "/exit"]), auto=True)

    assert (tmp_path / "cli.txt").read_text() == "approved"
    assert "Approval mode: auto" in child.requests[0].system


def test_timed_out_delegate_handler_is_drained_before_role_returns(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from logpose import tool

    started, release, finished = Event(), Event(), Event()

    @tool
    def slow_write() -> str:
        """Write a temporary marker after the test releases execution."""
        started.set()
        assert release.wait(2)
        (tmp_path / "joined.txt").write_text("done")
        finished.set()
        return "written"

    child = FakeProvider([wants(call("slow_write")), says("finished")])
    backend = replace(backend_for(), spawn_provider=lambda: child)
    cfg = replace(config, db_file=tmp_path / "db", tool_timeout=0.001)
    runner = RoleRunner(lambda: backend, [slow_write], cfg)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(runner.run, "lilith", "write")
        assert started.wait(2)
        try:
            assert not finished.wait(0.05)
            assert not future.done(), "delegate returned while its handler could still write"
        finally:
            release.set()
        future.result(2)
    assert finished.is_set()
    assert "execution limit" in repr(child.requests[1].messages)


def test_parallel_approvals_are_serialized_and_attributed(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from vegapunk import terminal

    monkeypatch.setattr("vegapunk.approval.sys.stdin.isatty", lambda: True)
    entered, release, second_started = Event(), Event(), Event()
    actors = []
    approver = CLIApprover()

    def ask(name, arguments):
        actors.append(terminal.ACTOR.get())
        if len(actors) == 1:
            entered.set()
            assert release.wait(2)
        return "yes"

    monkeypatch.setattr(approver, "_ask", ask)

    def approve(actor):
        with terminal.actor(actor):
            if actor == "Atlas":
                second_started.set()
            return approver.approve("run_shell", {"command": "pwd"})

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(approve, "Lilith")
        assert entered.wait(2)
        second = pool.submit(approve, "Atlas")
        assert second_started.wait(2)
        assert actors == ["Lilith"]
        release.set()
        assert first.result(2).allow
        assert second.result(2).allow
    assert actors == ["Lilith", "Atlas"]


def test_cancelling_turn_releases_live_approval_menu():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    ready = Event()
    approver = CLIApprover()
    original = approver._watch_cancel

    async def watch(app):
        ready.set()
        await original(app)

    approver._watch_cancel = watch
    with create_pipe_input() as pipe, ThreadPoolExecutor(1) as pool:
        result = pool.submit(approver._ask, "run_shell", {}, input=pipe, output=DummyOutput())
        assert ready.wait(2)
        approver.cancel_pending()
        assert result.result(2) is None
    from vegapunk.gate import ApprovalCancelled

    with pytest.raises(ApprovalCancelled):
        approver.approve("write_file", {})
    approver.begin_turn()
    approver.policy.auto = True
    assert approver.approve("write_file", {}).allow
