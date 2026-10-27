"""Persistent input, FIFO follow-ups, and safe handoff to approval widgets."""

import asyncio
from threading import Event

import pytest

from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from vegapunk import terminal
from vegapunk.prompter import PromptToolkitPrompter


async def until(predicate) -> None:
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.mark.parametrize("submit", ["\r", "\t"])
def test_busy_composer_queues_fifo_and_preserves_unsubmitted_draft_cursor(submit):
    async def scenario(pipe):
        release, started = Event(), Event()
        prompter = PromptToolkitPrompter(history=InMemoryHistory(), input=pipe,
                                        output=DummyOutput())

        def action():
            started.set()
            assert release.wait(3)

        run = asyncio.create_task(prompter._run_turn(action, release.set))
        await until(lambda: started.is_set() and prompter._session.app.is_running)
        pipe.send_text(f"second{submit}/model codex gpt-5.5\r")
        await until(lambda: len(prompter.queued) == 2)
        assert not run.done()
        pipe.send_text("draft\x1b[D")
        await until(lambda: prompter._session.default_buffer.cursor_position == 4)
        release.set()
        assert await run is None
        assert prompter.queued == ("second", "/model codex gpt-5.5")
        assert prompter._draft.text == "draft"
        assert prompter._draft.cursor_position == 4
        assert prompter.prompt() == "second"
        assert prompter.prompt() == "/model codex gpt-5.5"
        assert not terminal.composer_active()

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


def test_approval_handoff_restores_current_draft(monkeypatch):
    from vegapunk.approval import CLIApprover

    monkeypatch.setattr("vegapunk.approval.sys.stdin.isatty", lambda: True)

    async def scenario(pipe):
        start_approval, menu_ready = Event(), Event()
        prompter = PromptToolkitPrompter(history=InMemoryHistory(), input=pipe,
                                        output=DummyOutput())

        class PipeApprover(CLIApprover):
            def _ask(self, name, arguments):
                return super()._ask(name, arguments, input=pipe, output=DummyOutput())

            async def _watch_cancel(self, app):
                menu_ready.set()
                await super()._watch_cancel(app)

        approver = PipeApprover()

        def action():
            assert start_approval.wait(3)
            assert approver.approve("run_shell", {"command": "pwd"}).allow

        run = asyncio.create_task(prompter._run_turn(action, approver.cancel_pending))
        await until(lambda: prompter._session.app.is_running)
        pipe.send_text("keep this draft")
        await until(lambda: prompter._session.default_buffer.text == "keep this draft")
        start_approval.set()
        await until(menu_ready.is_set)
        pipe.send_text("\r")
        assert await run is None
        assert prompter._draft.text == "keep this draft"
        assert prompter.queued == ()

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


def test_question_picker_handoff_preserves_draft(monkeypatch):
    from vegapunk.questions import CLIQuestioner

    monkeypatch.setattr("vegapunk.questions.sys.stdin.isatty", lambda: True)

    async def scenario(pipe):
        start_question, menu_ready = Event(), Event()
        prompter = PromptToolkitPrompter(history=InMemoryHistory(), input=pipe,
                                        output=DummyOutput())

        class PipeQuestioner(CLIQuestioner):
            def _choose(self, question, options, preferred_option, **kwargs):
                menu_ready.set()
                return super()._choose(question, options, preferred_option,
                                       input=pipe, output=DummyOutput(), **kwargs)

        questioner = PipeQuestioner()

        def action():
            assert start_question.wait(3)
            answer = questioner.ask("Which service?", ["Keep", "Change"], "Keep")
            assert '"Keep"' in answer

        run = asyncio.create_task(prompter._run_turn(action, lambda: None))
        await until(lambda: prompter._session.app.is_running)
        pipe.send_text("continue typing")
        await until(lambda: prompter._session.default_buffer.text == "continue typing")
        start_question.set()
        await until(menu_ready.is_set)
        pipe.send_text("\r")
        assert await run is None
        assert prompter._draft.text == "continue typing"
        assert prompter.queued == ()

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


def test_eof_cancels_and_drains_worker_before_exit():
    async def scenario(pipe):
        cancelled, finished = Event(), Event()
        prompter = PromptToolkitPrompter(history=InMemoryHistory(), input=pipe,
                                        output=DummyOutput())

        def action():
            assert cancelled.wait(3)
            finished.set()

        run = asyncio.create_task(prompter._run_turn(action, cancelled.set))
        await until(lambda: prompter._session.app.is_running)
        pipe.send_text("\x04")
        assert await run is None
        assert finished.is_set()
        assert prompter.prompt() == "/exit"
        assert not terminal.composer_active()

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


def test_ctrl_c_cancels_real_session_stream_and_preserves_queue():
    from tests.fake_provider import says, session_for
    from vegapunk.cli import _render_reply
    from vegapunk.interactive_agent import TurnCancelled

    async def scenario(pipe):
        session = session_for([says("too late", delay=5)])
        session.prepare_turn()
        prompter = PromptToolkitPrompter(history=InMemoryHistory(), input=pipe,
                                        output=DummyOutput())
        run = asyncio.create_task(prompter._run_turn(
            lambda: _render_reply(session.send("first"), session.renderer), session.cancel))
        await until(lambda: prompter._session.app.is_running
                    and session.backend.provider.call_count == 1)
        pipe.send_text("follow-up\r")
        await until(lambda: len(prompter.queued) == 1)
        pipe.send_text("\x03")
        assert isinstance(await run, TurnCancelled)
        assert prompter.queued == ("follow-up",)
        assert session.messages == []
        await asyncio.to_thread(session.close)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


def test_rich_output_uses_current_stream_and_preserves_tool_preamble(monkeypatch):
    import io
    from vegapunk.render import RichRenderer

    renderer = RichRenderer()
    output, trace = io.StringIO(), io.StringIO()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("sys.stderr", trace)
    terminal.set_input_runner(lambda callback: callback())
    try:
        renderer.reply_delta("Inspecting the service.")
        renderer.tool_call("read_file", {"path": "config"})
        renderer.reply_delta("The check passed.")
        renderer.reply_end()
    finally:
        terminal.set_input_runner(None)
    assert "Inspecting the service." in output.getvalue()
    assert "The check passed." in output.getvalue()
    assert "read_file" in trace.getvalue()


def test_cancel_before_executor_consumes_stream_does_not_start_provider():
    from tests.fake_provider import says, session_for
    from vegapunk.cli import _render_reply
    from vegapunk.interactive_agent import TurnCancelled

    async def scenario(pipe):
        session = session_for([says("must not execute")])
        session.prepare_turn()  # CLI does this synchronously before handing off.
        release = Event()
        cancelled = Event()
        prompter = PromptToolkitPrompter(history=InMemoryHistory(), input=pipe,
                                        output=DummyOutput())

        def action():
            assert release.wait(3)
            _render_reply(session.send("first"), session.renderer)

        def cancel():
            session.cancel()
            cancelled.set()

        run = asyncio.create_task(prompter._run_turn(action, cancel))
        await until(lambda: prompter._session.app.is_running)
        pipe.send_text("\x03")
        await until(cancelled.is_set)
        release.set()
        assert isinstance(await run, TurnCancelled)
        assert session.backend.provider.call_count == 0
        await asyncio.to_thread(session.close)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


def test_primary_sync_handler_is_joined_before_cancelled_turn_returns():
    from logpose import tool
    from tests.fake_provider import call, session_for, wants
    from vegapunk.cli import _render_reply
    from vegapunk.interactive_agent import TurnCancelled

    async def scenario(pipe):
        started, release, finished = Event(), Event(), Event()

        @tool
        def blocked_operation() -> str:
            """Wait for the test to release an in-flight synchronous operation."""
            started.set()
            assert release.wait(3)
            finished.set()
            return "finished"

        session = session_for([wants(call("blocked_operation"))], tools=[blocked_operation])
        session.prepare_turn()
        prompter = PromptToolkitPrompter(history=InMemoryHistory(), input=pipe,
                                        output=DummyOutput())
        run = asyncio.create_task(prompter._run_turn(
            lambda: _render_reply(session.send("first"), session.renderer), session.cancel))
        await until(lambda: started.is_set() and prompter._session.app.is_running)
        pipe.send_text("\x03")
        await until(lambda: prompter._stopping)
        assert not run.done()
        session.cancel()  # A repeated interrupt must not detach the handler.
        release.set()
        assert isinstance(await run, TurnCancelled)
        assert finished.is_set()
        await asyncio.to_thread(session.close)

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))


def test_input_failure_releases_pending_approval_handoff(monkeypatch):
    import pytest
    from vegapunk.approval import CLIApprover

    monkeypatch.setattr("vegapunk.approval.sys.stdin.isatty", lambda: True)

    async def scenario(pipe):
        requested, finished = Event(), Event()
        approver = CLIApprover()
        prompter = PromptToolkitPrompter(history=InMemoryHistory(), input=pipe,
                                        output=DummyOutput())

        async def failed_prompt(**kwargs):
            await until(requested.is_set)
            await asyncio.sleep(0.02)  # Let handoff start waiting for the absent UI.
            raise OSError("input disconnected")

        monkeypatch.setattr(prompter._session, "prompt_async", failed_prompt)

        def action():
            requested.set()
            try:
                approver.approve("write_file", {})
            finally:
                finished.set()

        with pytest.raises(OSError, match="input disconnected"):
            await prompter._run_turn(action, approver.cancel_pending)
        assert finished.is_set()
        assert not terminal.composer_active()

    with create_pipe_input() as pipe:
        asyncio.run(scenario(pipe))
