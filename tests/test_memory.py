"""Tests for Vegapunk's long-term memory — store + the remember tool.

The memory path is read live via ``memory.memory_path()``; ``config`` is frozen,
so we monkeypatch that helper to a tmp file rather than mutating config (the same
trick ``test_filesystem_tools.py`` uses for the workspace root).
"""

from __future__ import annotations

import pytest

from vegapunk.memory import as_system_block, load_memory, save_memory
from vegapunk.tools import ALL_TOOLS
from vegapunk.tools.memory import remember


@pytest.fixture
def mem(tmp_path, monkeypatch):
    path = tmp_path / ".vegapunk" / "memory.md"
    monkeypatch.setattr("vegapunk.memory.memory_path", lambda: path)
    return path


def test_load_memory_empty_when_no_file(mem):
    assert load_memory() == ""  # nothing remembered yet, no file on disk
    assert not mem.exists()


def test_save_memory_appends_dated_bullet_and_creates_file(mem):
    result = save_memory("prefers ruff over flake8")

    assert "prefers ruff over flake8" in result
    contents = mem.read_text()
    assert "prefers ruff over flake8" in contents
    assert contents.startswith("- [")  # dated bullet, parent dir auto-created


def test_save_memory_accumulates_across_calls(mem):
    save_memory("first fact")
    save_memory("second fact")

    contents = load_memory()
    assert "first fact" in contents
    assert "second fact" in contents
    assert contents.count("\n") == 2  # one bullet per fact


def test_load_memory_degrades_on_unreadable_file(mem, capsys):
    # A hand-mangled, non-UTF-8 memory file must not crash startup — load_memory
    # runs inline while seeding the system prompt.
    mem.parent.mkdir(parents=True, exist_ok=True)
    mem.write_bytes(b"\xff\xfe not valid utf-8")

    assert load_memory() == ""  # degrades to "no memory" instead of raising
    assert "could not read" in capsys.readouterr().err  # but says so on stderr


def test_save_memory_empty_is_noop(mem):
    result = save_memory("   ")  # whitespace only

    assert "Nothing to remember" in result
    assert not mem.exists()  # no empty bullet written


def test_as_system_block_empty_when_no_memory(mem):
    assert as_system_block() == ""


def test_as_system_block_contains_memory_when_present(mem):
    save_memory("deploys from main")

    block = as_system_block()
    assert "deploys from main" in block
    assert "remember about the user" in block  # labelled for the model


def test_remember_tool_saves_and_confirms(mem):
    result = remember("works in the Pacific timezone")

    assert "works in the Pacific timezone" in result
    assert "works in the Pacific timezone" in mem.read_text()


def test_remember_tool_registered_and_unguarded():
    tool = next(t for t in ALL_TOOLS if t.name == "remember")
    assert tool.guarded is False  # writes its own notebook, not the workspace
    schema = tool.to_schema()["function"]["parameters"]
    assert schema["properties"]["fact"] == {"type": "string"}
    assert schema["required"] == ["fact"]


def test_system_prompt_composition_includes_memory(mem):
    # Exercises the exact expression cli.main uses to seed the session.
    from vegapunk.config import config

    save_memory("uses zsh")
    composed = config.system_prompt + as_system_block()

    assert "uses zsh" in composed


def test_cli_main_seeds_session_with_memory(mem, monkeypatch):
    # Pin the wiring: cli.main must construct the Session with memory folded into
    # the system prompt. Capture the Session it builds (no model/TTY needed); an
    # immediate EOFError ends the REPL right after the session is created.
    from vegapunk import cli
    from vegapunk.prompter import ScriptedPrompter

    save_memory("prefers tabs over spaces")

    captured: dict[str, str] = {}

    class _CapturingSession:
        def __init__(self, brain, tools, system_prompt="", **kwargs):
            captured["system_prompt"] = system_prompt
            self.brain = brain  # main() reads session.brain for the banner

    monkeypatch.setattr("vegapunk.cli.Session", _CapturingSession)

    cli.main(prompter=ScriptedPrompter([EOFError]))

    assert "prefers tabs over spaces" in captured["system_prompt"]
