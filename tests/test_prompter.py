"""Tests for the prompt_toolkit-backed prompter — deterministic, no real TTY.

Two layers, because prompt_toolkit applies completion / ghost-text acceptance in
the *renderer* (which DummyOutput no-ops): line submission and the custom newline
key-bindings are driven through a pipe; the completer and auto-suggest are checked
at the object layer.
"""

from __future__ import annotations

import pytest
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from vegapunk.prompter import PromptToolkitPrompter, ScriptedPrompter


def _prompter(tmp_path, inp):
    return PromptToolkitPrompter(history=InMemoryHistory(), input=inp, output=DummyOutput())


def test_plain_line_submits(tmp_path):
    with create_pipe_input() as inp:
        inp.send_text("hello world\r")
        assert _prompter(tmp_path, inp).prompt() == "hello world"


def test_ctrl_j_inserts_newline(tmp_path):
    # "\n" is Ctrl-J, bound to insert a newline; "\r" is Enter, which submits.
    with create_pipe_input() as inp:
        inp.send_text("line1\nline2\r")
        assert _prompter(tmp_path, inp).prompt() == "line1\nline2"


def test_esc_enter_inserts_newline(tmp_path):
    # "\x1b\r" is Esc-Enter, bound to insert a newline.
    with create_pipe_input() as inp:
        inp.send_text("first\x1b\rmore\r")
        assert _prompter(tmp_path, inp).prompt() == "first\nmore"


def test_shift_tab_toggles_approval_without_submitting_or_changing_the_draft():
    from vegapunk.approval import ApprovalPolicy

    policy = ApprovalPolicy()
    with create_pipe_input() as inp:
        inp.send_text("keep this\x1b[Z intact\r")
        prompter = PromptToolkitPrompter(
            history=InMemoryHistory(),
            input=inp,
            output=DummyOutput(),
            toggle_approval=policy.toggle,
        )

        assert prompter.prompt() == "keep this intact"
        assert policy.mode == "auto"


def test_history_persists_to_db(tmp_path):
    # Drive a prompter with the default DbHistory, then prove a fresh DbHistory
    # reads the entry back from the (conftest-isolated) database.
    from vegapunk.db_history import DbHistory

    with create_pipe_input() as inp:
        inp.send_text("remember me\r")
        PromptToolkitPrompter(input=inp, output=DummyOutput()).prompt()
    assert "remember me" in list(DbHistory().load_history_strings())


def _complete(text: str) -> list[str]:
    """What the live prompter would offer for ``text``, cursor at the end."""
    completer = PromptToolkitPrompter(history=InMemoryHistory())._session.completer
    return [c.text for c in completer.get_completions(Document(text, len(text)), CompleteEvent())]


def test_completer_suggests_commands_and_leaves_prose_alone():
    assert _complete("/cl") == []
    # A sentence is what you're mostly typing; popping a menu into it would be
    # worse than offering nothing.
    assert _complete("tell me ex") == []


def test_completer_offers_backends_after_a_model_command():
    for line in ("/model ",):
        offered = _complete(line)
        assert "claude" in offered and "codex" in offered and "local" in offered


def test_completer_narrows_backends_by_what_is_typed():
    assert _complete("/model cla") == ["claude", "claude-code"]


def test_completer_offers_effort_levels():
    assert _complete("/effort ") == ["low", "medium", "high", "xhigh", "max"]


def test_schedule_dropdown_exposes_all_supported_subcommands():
    assert set(_complete("/schedule ")) == {
        "list", "history", "notebook", "drafts", "add", "profile", "remove", "grant",
        "revoke", "permissions", "actions", "resolve-action", "autonomy"}
    assert _complete("/schedule not") == ["notebook"]


@pytest.mark.parametrize(("line", "expected"), [
    ("/schedule profile abc ", ["general", "moltbook"]),
    ("/schedule profile abc mol", ["moltbook"]),
    ("/schedule grant abc ", ["moltbook.reply_own"]),
    ("/schedule revoke abc mol", ["moltbook.reply_own"]),
    ("/schedule add 300 ", ["--profile"]),
    ("/schedule add 300 --pr", ["--profile"]),
    ("/schedule add 300 --profile ", ["general", "moltbook"]),
    ("/schedule add 300 --profile mol", ["moltbook"]),
    ("/schedule resolve-action abc ", ["accepted", "rejected"]),
    ("/schedule resolve-action abc rej", ["rejected"]),
    ("/schedule add ", []),
    ("/schedule add 300 Explore ", []),
    ("/schedule add 300 --profile moltbook ", []),
    ("/schedule add 300 --profile moltbook Explore ", []),
    ("/schedule profile abc moltbook ", []),
    ("/schedule grant abc moltbook.reply_own ", []),
    ("/schedule resolve-action abc accepted ", []),
    ("/schedule list ", []),
])
def test_schedule_dropdown_suggests_only_contextual_options(line, expected):
    assert _complete(line) == expected


def test_schedule_dropdown_offers_live_task_ids_without_mutating_tasks():
    from vegapunk import scheduler
    scheduler.add_task("ordinary", 300)
    scheduler.add_task("social", 300, profile="moltbook")
    ids = {task.id for task in scheduler.list_tasks()}
    for sub in ("profile", "remove", "grant", "revoke", "history", "notebook", "permissions", "actions"):
        assert set(_complete(f"/schedule {sub} ")) == ids
        task_id = sorted(ids)[0]
        assert task_id in _complete(f"/schedule {sub} {task_id[:8]}")
    assert {task.id for task in scheduler.list_tasks()} == ids


def test_schedule_dropdown_offers_retained_history_notes_and_action_ids():
    from vegapunk import db, scheduler, task_history
    scheduler.add_task("old social", 300, profile="moltbook")
    task = scheduler.list_tasks()[0]
    run_id = task_history.begin(task)
    task_history.finish(task, run_id, "completed", "done")
    stamp = db.utcnow()
    db.execute("INSERT INTO moltbook_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("source", task.id, "tag", "post", "remote", "/posts/remote", "peer", "quote",
                "hash", run_id, run_id, stamp, stamp, 1))
    note_id, action_id = "a" * 32, "b" * 32
    db.execute("INSERT INTO moltbook_notes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (note_id, task.id, "tag", run_id, "observation", "topic", "learning", "medium", "source",
                "quote", "", "active", "", "fingerprint", stamp, stamp, "", "", ""))
    db.execute("INSERT INTO moltbook_actions (id,task_id,run_id,last_run_id,account_id,tool_call_id,post_id,parent_id,"
               "content,content_hash,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (action_id, task.id, run_id, run_id, "account", "call", "post", "parent", "reply", "hash",
                "unknown", stamp, stamp))
    scheduler.remove_task(task.id)
    assert _complete("/schedule history ") == [task.id]
    assert _complete("/schedule actions ") == [task.id]
    assert set(_complete("/schedule notebook ")) == {task.id, note_id}
    assert _complete("/schedule notebook aaaa") == [note_id]
    assert _complete("/schedule resolve-action ") == [action_id]
    assert _complete("/schedule profile ") == []


def test_schedule_completion_database_failure_does_not_break_prompt(monkeypatch):
    from vegapunk import db
    def unavailable(*args, **kwargs):
        raise db.StoreError("offline")
    monkeypatch.setattr(db, "query", unavailable)
    assert _complete("/schedule notebook ") == []
    assert _complete("/schedule resolve-action ") == []
    assert _complete("/schedule profile abc ") == ["general", "moltbook"]


def test_completer_offers_saved_sessions_for_load_and_forget():
    from vegapunk.session_store import save_session

    save_session("alpha-chat", [])
    save_session("beta-chat", [])

    assert set(_complete("/sessions ")) == {"alpha-chat", "beta-chat", "remove"}
    assert _complete("/sessions remove al") == ["alpha-chat"]
    assert "remove" in _complete("/sessions ")


def test_completer_never_makes_a_network_call_for_model_ids(monkeypatch):
    """Completion runs on every keystroke, so it may only offer ids already
    fetched — blocking the line you are typing on an HTTP round trip would be
    far worse than offering nothing until /model has been run once."""
    def _boom(*args, **kwargs):
        raise AssertionError("completion must not fetch models")

    monkeypatch.setattr("vegapunk.backend.available_models", _boom)

    assert _complete("/model claude ") == []


def test_completer_offers_cached_model_ids_once_they_are_known(monkeypatch):
    monkeypatch.setattr("vegapunk.prompter.cached_models", lambda name: ["claude-opus-5", "x"])

    assert _complete("/model claude claude-") == ["claude-opus-5"]


def test_auto_suggest_from_history():
    history = InMemoryHistory()
    history.append_string("hello there world")
    suggestion = AutoSuggestFromHistory().get_suggestion(Buffer(history=history), Document("hel", 3))
    assert suggestion is not None and suggestion.text == "lo there world"


def test_scripted_prompter_yields_then_eof():
    p = ScriptedPrompter(["first", "second"])
    assert p.prompt() == "first"
    assert p.prompt() == "second"
    with pytest.raises(EOFError):
        p.prompt()


def test_scripted_prompter_raises_queued_exception():
    p = ScriptedPrompter([KeyboardInterrupt, "after"])
    with pytest.raises(KeyboardInterrupt):
        p.prompt()
    assert p.prompt() == "after"


def test_status_callable_is_wired_to_the_bottom_toolbar():
    status = lambda: " gemma · my-chat"  # noqa: E731 — mirrors the CLI's wiring
    prompter = PromptToolkitPrompter(history=InMemoryHistory(), status=status)
    assert prompter._session.bottom_toolbar is status  # re-evaluated per render


def test_prompt_message_is_plain_off_a_tty():
    # Under pytest stdout isn't a TTY and the suite pins color mode "auto",
    # so the constructor must pick the plain string, not style tuples.
    prompter = PromptToolkitPrompter(history=InMemoryHistory())
    assert prompter._session.message == "❯ "


def test_prompt_message_is_gold_when_color_forced(monkeypatch):
    from dataclasses import replace

    from vegapunk import style

    monkeypatch.setattr("vegapunk.style.config", replace(style.config, color="always"))
    prompter = PromptToolkitPrompter(history=InMemoryHistory())
    assert prompter._session.message == [("bold fg:ansiyellow", "❯ ")]
