"""Tests for the agent's structured interactive-question capability."""

from __future__ import annotations

import asyncio
import time

import pytest
from logpose import Conversation, RunEnd, TextDelta, stream_sync, tool
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from vegapunk import questions
from vegapunk.cli import main
from vegapunk.gate import make_gate
from vegapunk.interactive_agent import (
    QUESTION_BATCH_BLOCKED,
    TEXT_QUESTION_CORRECTION,
    InteractiveAgent,
    _final_answer,
)
from vegapunk.loop import run
from vegapunk.prompter import ScriptedPrompter
from vegapunk.questions import CANCELLED, CUSTOM, UNAVAILABLE, CLIQuestioner, ScriptedQuestioner
from vegapunk.tools import ALL_TOOLS, GUARDED
from vegapunk.tools.questions import ask_user
from tests.fake_provider import FakeProvider, agent_for, call, says, session_for, wants

DOWN = "\x1b[B"
ENTER = "\r"
ESC = "\x1b\x1b"


@pytest.fixture(autouse=True)
def _clear_questioner():
    questions.set_questioner(None)
    yield
    questions.set_questioner(None)


class _PipeQuestioner(CLIQuestioner):
    """Drive both halves of the real terminal UI through prompt_toolkit pipes."""

    def __init__(self, menu_keys: str, custom_text: str = "") -> None:
        super().__init__()
        self._menu_keys = menu_keys
        self._custom_text = custom_text

    def _choose(self, question, options, preferred_option, *, input=None, output=None):
        with create_pipe_input() as pipe:
            pipe.send_text(self._menu_keys)
            return super()._choose(
                question, options, preferred_option, input=pipe, output=DummyOutput()
            )

    def _custom_answer(self, *, input=None, output=None):
        with create_pipe_input() as pipe:
            pipe.send_text(self._custom_text)
            return super()._custom_answer(input=pipe, output=DummyOutput())


def test_recommended_option_is_preselected_and_returned(monkeypatch):
    monkeypatch.setattr("vegapunk.questions.sys.stdin.isatty", lambda: True)
    questioner = _PipeQuestioner(ENTER)

    result = questioner.ask("Choose a release path", ["Ship now", "Wait"], "Wait")

    assert 'User selected option: "Wait"' in result


def test_other_opens_a_free_text_prompt(monkeypatch):
    monkeypatch.setattr("vegapunk.questions.sys.stdin.isatty", lambda: True)
    questioner = _PipeQuestioner(DOWN + DOWN + ENTER, "Use a canary\r")

    result = questioner.ask("Choose a release path", ["Ship now", "Wait"], "Ship now")

    assert 'User selected custom response: "Use a canary"' in result


def test_dismissing_the_picker_returns_a_non_retrying_result(monkeypatch):
    monkeypatch.setattr("vegapunk.questions.sys.stdin.isatty", lambda: True)

    assert _PipeQuestioner(ESC).ask("Continue?", ["Yes"], "Yes") == CANCELLED


def test_empty_custom_response_cancels_the_question(monkeypatch):
    monkeypatch.setattr("vegapunk.questions.sys.stdin.isatty", lambda: True)

    assert _PipeQuestioner(DOWN + ENTER, "\x04").ask("Continue?", ["Yes"], "Yes") == CANCELLED


def test_non_interactive_questioner_never_blocks(monkeypatch):
    monkeypatch.setattr("vegapunk.questions.sys.stdin.isatty", lambda: False)

    assert CLIQuestioner().ask("Continue?", ["Yes"], "Yes") == UNAVAILABLE


def test_cli_installs_and_clears_the_question_handler(monkeypatch):
    installed = []
    monkeypatch.setattr("vegapunk.cli.set_questioner", installed.append)

    main(prompter=ScriptedPrompter(["/exit"]), session=session_for([]))

    assert isinstance(installed[0], CLIQuestioner)
    assert installed[-1] is None


def test_tool_returns_the_scripted_answer_and_records_the_question():
    questioner = ScriptedQuestioner(["Wait"])
    questions.set_questioner(questioner)

    result = ask_user("Choose a release path", ["Ship now", "Wait"], "Ship now")

    assert 'User selected option: "Wait"' in result
    assert "call ask_user again" in result
    assert questioner.calls == [
        ("Choose a release path", ["Ship now", "Wait"], "Ship now")
    ]


def test_selected_answer_returns_to_the_model_and_the_turn_continues():
    questioner = ScriptedQuestioner(["Wait"])
    questions.set_questioner(questioner)
    agent, provider = agent_for(
        [
            wants(
                call(
                    "ask_user",
                    {
                        "question": "Choose a release path",
                        "options": ["Ship now", "Wait"],
                        "preferred_option": "Ship now",
                    },
                )
            ),
            says("I will wait."),
        ],
        tools=[ask_user],
    )

    assert run(agent, "Prepare the release") == "I will wait."
    result = provider.requests[1].messages[-1].content[0]
    assert 'User selected option: "Wait"' in result.content
    assert "call ask_user again" in result.content
    assert result.is_error is False


def test_multiple_interview_questions_stay_in_the_picker_flow():
    questioner = ScriptedQuestioner(["Science fiction", "No horror"])
    questions.set_questioner(questioner)
    agent, provider = agent_for(
        [
            wants(
                call(
                    "ask_user",
                    {
                        "question": "Which genre sounds best?",
                        "options": ["Science fiction", "Comedy"],
                        "preferred_option": "Science fiction",
                    },
                )
            ),
            wants(
                call(
                    "ask_user",
                    {
                        "question": "Should I avoid horror?",
                        "options": ["No horror", "Horror is fine"],
                        "preferred_option": "No horror",
                    },
                )
            ),
            says("Watch Arrival."),
        ],
        tools=[ask_user],
    )

    assert run(agent, "Interview me, then recommend a movie") == "Watch Arrival."
    assert [call[0] for call in questioner.calls] == [
        "Which genre sounds best?",
        "Should I avoid horror?",
    ]
    first_result = provider.requests[1].messages[-1].content[0].content
    assert 'User selected option: "Science fiction"' in first_result
    assert "call ask_user again" in first_result


def test_textual_follow_up_options_are_hidden_and_retried_as_a_picker_call():
    questioner = ScriptedQuestioner(["Science fiction", "No horror"])
    questions.set_questioner(questioner)
    provider = FakeProvider(
        [
            wants(
                call(
                    "ask_user",
                    {
                        "question": "Which genre sounds best?",
                        "options": ["Science fiction", "Comedy"],
                        "preferred_option": "Science fiction",
                    },
                )
            ),
            says("Here are the options:\nSerious\nLight"),
            wants(
                call(
                    "ask_user",
                    {
                        "question": "Which tone should I use?",
                        "options": ["Serious", "Light"],
                        "preferred_option": "Serious",
                    },
                )
            ),
            says("FINAL: Watch Who Am I? for a cerebral thriller."),
        ]
    )
    agent = InteractiveAgent(
        provider, system="SYS", tools=[ask_user], on_tool_call=make_gate(None)
    )

    events = list(
        stream_sync(agent, "Interview me, then recommend a movie", conversation=Conversation())
    )

    assert [event.text for event in events if isinstance(event, TextDelta)] == [
        "Watch Who Am I? for a cerebral thriller."
    ]
    assert questioner.calls == [
        ("Which genre sounds best?", ["Science fiction", "Comedy"], "Science fiction"),
        ("Which tone should I use?", ["Serious", "Light"], "Serious"),
    ]
    assert TEXT_QUESTION_CORRECTION in provider.requests[2].messages[-1].text
    assert isinstance(events[-1], RunEnd)
    assert events[-1].result.text == "Watch Who Am I? for a cerebral thriller."


def test_completed_interview_answer_requires_and_strips_the_final_marker():
    assert _final_answer("FINAL: My pick is Arrival for thoughtful science fiction.") == (
        "My pick is Arrival for thoughtful science fiction."
    )
    assert _final_answer("Here are the options:\nAction\nComedy") is None


def test_question_is_not_limited_by_the_shared_tool_timeout():
    class SlowQuestioner(questions.Questioner):
        def ask(self, question, options, preferred_option):
            time.sleep(0.02)
            return 'User selected option: "Wait"'

    questions.set_questioner(SlowQuestioner())
    provider = FakeProvider(
        [
            wants(
                call(
                    "ask_user",
                    {
                        "question": "Choose a release path",
                        "options": ["Ship now", "Wait"],
                        "preferred_option": "Ship now",
                    },
                )
            ),
            says("FINAL: I will wait."),
        ]
    )
    agent = InteractiveAgent(
        provider, system="SYS", tools=[ask_user], tool_timeout=0.001, on_tool_call=make_gate(None)
    )

    assert run(agent, "Prepare the release") == "I will wait."
    assert 'User selected option: "Wait"' in provider.requests[1].messages[-1].content[0].content


def test_question_blocks_sibling_tools_until_the_model_sees_the_answer():
    calls: list[str] = []

    @tool
    def deploy() -> str:
        """Perform the dependent action."""
        calls.append("deploy")
        return "deployed"

    questions.set_questioner(ScriptedQuestioner(["Wait"]))
    provider = FakeProvider(
        [
            wants(
                call(
                    "ask_user",
                    {
                        "question": "Choose a release path",
                        "options": ["Ship now", "Wait"],
                        "preferred_option": "Ship now",
                    },
                    id="question",
                ),
                call("deploy", id="deploy"),
            ),
            says("FINAL: I will wait."),
        ]
    )
    agent = InteractiveAgent(
        provider, system="SYS", tools=[ask_user, deploy], on_tool_call=make_gate(None)
    )

    assert run(agent, "Prepare the release") == "I will wait."
    assert calls == []
    results = provider.requests[1].messages[-1].content
    assert 'User selected option: "Wait"' in results[0].content
    assert results[1].content == QUESTION_BATCH_BLOCKED
    assert [result.is_error for result in results] == [False, False]


def test_unattended_question_returns_an_unavailable_result():
    agent, provider = agent_for(
        [
            wants(
                call(
                    "ask_user",
                    {
                        "question": "Choose a release path",
                        "options": ["Ship now", "Wait"],
                        "preferred_option": "Ship now",
                    },
                )
            ),
            says("I need your decision."),
        ],
        tools=[ask_user],
    )

    assert run(agent, "Prepare the release") == "I need your decision."
    assert provider.requests[1].messages[-1].content[0].content == UNAVAILABLE


@pytest.mark.parametrize(
    ("options", "preferred", "message"),
    [
        ([], "", "between 1 and 5"),
        (["a", "b", "c", "d", "e", "f"], "a", "between 1 and 5"),
        (["a", "a"], "a", "distinct"),
        ([CUSTOM], CUSTOM, "reserved custom-answer value"),
        (["a", ""], "a", "empty choices"),
        (["a"], "b", "must exactly match"),
    ],
)
def test_tool_rejects_invalid_choice_sets(options, preferred, message):
    with pytest.raises(ValueError, match=message):
        ask_user("Choose", options, preferred)


def test_tool_schema_is_registered_and_unguarded():
    made = next(tool for tool in ALL_TOOLS if tool.name == "ask_user")

    assert made.input_schema["required"] == ["question", "options", "preferred_option"]
    assert made.input_schema["properties"]["options"]["type"] == "array"
    assert "ask_user" not in GUARDED


def test_invalid_tool_arguments_are_returned_as_a_correctable_error():
    made = next(tool for tool in ALL_TOOLS if tool.name == "ask_user")

    with pytest.raises(Exception, match="between 1 and 5"):
        asyncio.run(made.invoke({"question": "Choose", "options": [], "preferred_option": ""}))
