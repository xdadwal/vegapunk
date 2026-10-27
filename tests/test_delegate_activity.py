"""Provider usage and distinct live role progress, without model/network calls."""

import io
from dataclasses import replace

from logpose import Usage
from rich.console import Console

from tests.fake_provider import FakeProvider, backend_for, call, says, wants
from vegapunk import terminal
from vegapunk.config import config
from vegapunk.delegate_activity import DelegateActivity, DelegateDisplay
from vegapunk.delegation import RoleRunner
from vegapunk.tools import ALL_TOOLS


def test_role_usage_accumulates_provider_turns_once(tmp_path):
    child = FakeProvider([
        wants(call("list_directory", {"path": "."}),
              usage=Usage(input_tokens=100, output_tokens=10, cache_read_input_tokens=25)),
        says("checked", usage=Usage(input_tokens=150, output_tokens=20)),
    ])
    backend = replace(backend_for(), spawn_provider=lambda: child)
    activity = DelegateActivity()
    RoleRunner(lambda: backend, ALL_TOOLS, replace(config, db_file=tmp_path / "db"),
               activity=activity).run("shaka", "inspect")

    run, = activity.snapshot()
    assert (run.input_tokens, run.output_tokens, run.cache_read) == (250, 30, 25)
    assert run.status == "completed"
    assert run.ended is not None
    assert "250 in / 30 out" in activity.lines()[0]


def test_same_role_has_distinct_ids_elapsed_time_and_unknown_usage():
    now = [10.0]
    activity = DelegateActivity(clock=lambda: now[0])
    first = activity.start("Atlas", "model")
    second = activity.start("Atlas", "model")
    assert first != second
    activity.status(first, "approval: run_shell")
    now[0] = 14.5
    assert "4.5s" in activity.lines()[0]
    assert "usage not reported" in activity.lines()[0]
    activity.finish(first, "completed")
    now[0] = 20.0
    assert "4.5s" in activity.lines()[0]
    assert "10.0s" in activity.lines()[1]


def test_failed_role_keeps_final_status_and_elapsed_time(tmp_path):
    child = FakeProvider(says("", error=RuntimeError("offline")))
    backend = replace(backend_for(), spawn_provider=lambda: child)
    activity = DelegateActivity()
    import pytest

    with pytest.raises(RuntimeError, match="offline"):
        RoleRunner(lambda: backend, ALL_TOOLS, replace(config, db_file=tmp_path / "db"),
                   activity=activity).run("shaka", "inspect")
    run, = activity.snapshot()
    assert run.status == "failed"
    assert run.ended is not None


def test_terminal_panel_yields_to_interaction_and_summary_is_plain():
    output = io.StringIO()
    activity = DelegateActivity()
    display = DelegateDisplay(activity, Console(file=output, force_terminal=True, width=100))
    key = activity.start("Atlas", "model")
    terminal.set_pause_handler(display.pause)
    try:
        with terminal.LOCK:
            display._draw()
        assert display._live is not None
        with terminal.interaction():
            assert display._live is None
        activity.finish(key, "completed")
        display.close()
    finally:
        terminal.set_pause_handler(None)
    assert "1 agent · Atlas" in output.getvalue()
    assert "tokens n/a" in output.getvalue()


def test_non_terminal_completion_does_not_use_live_escape_sequences():
    output = io.StringIO()
    activity = DelegateActivity()
    display = DelegateDisplay(activity, Console(file=output, force_terminal=False))
    key = activity.start("Atlas", "model")
    activity.finish(key, "completed")
    display.close()
    assert "Atlas #1" in output.getvalue()
    assert "\x1b" not in output.getvalue()


def test_primary_output_pauses_live_panel_before_printing():
    from vegapunk.render import RichRenderer

    output = io.StringIO()
    console = Console(file=output, force_terminal=True, width=100)
    activity = DelegateActivity()
    display = DelegateDisplay(activity, console)
    activity.start("Atlas", "model")
    terminal.set_pause_handler(display.pause)
    try:
        with terminal.LOCK:
            display._draw()
        assert display._live is not None
        renderer = RichRenderer(console, console)
        renderer.tool_result("read_file", {}, "visible evidence", False)
        assert display._live is None
        assert "visible evidence" in output.getvalue()
    finally:
        display.close()


def test_formatting_uses_the_selected_snapshot_during_history_pruning():
    activity = DelegateActivity()
    first = activity.start("first", "model")
    activity.finish(first, "completed")
    snapshot = activity.snapshot()
    for _ in range(25):
        key = activity.start("later", "model")
        activity.finish(key, "completed")
    assert "first #1" in activity.lines(snapshot)[0]
    assert all(run.id != first for run in activity.snapshot())


def test_completion_is_flushed_at_handoff_not_by_background_refresh():
    output = io.StringIO()
    activity = DelegateActivity()
    display = DelegateDisplay(activity, Console(file=output, force_terminal=False))
    key = activity.start("Atlas", "model")
    activity.finish(key, "completed")
    display._draw()
    assert output.getvalue() == ""
    terminal.set_pause_handler(display.pause)
    try:
        with terminal.interaction():
            assert "Atlas #1" in output.getvalue()
        before = output.getvalue()
        display._draw()
        assert output.getvalue() == before
    finally:
        display.close()


def test_compact_usage_identifies_unreported_roles():
    from logpose import TurnEnd

    activity = DelegateActivity()
    key = activity.start("Atlas", "model")
    activity.start("Lilith", "model")
    activity.usage(key, TurnEnd("tool_use", Usage(input_tokens=100, output_tokens=20)))
    assert "120 reported tok" in activity.compact()
    assert "1 n/a" in activity.compact()
