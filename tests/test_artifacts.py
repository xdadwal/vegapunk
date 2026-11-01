"""Artifact snapshots are durable, bounded, and session-scoped."""

import pytest
from vegapunk import artifacts, session_store


def identity(name):
    session_store.save_session(name, [])
    return session_store.session_identity(name)


def test_snapshot_pagination_and_session_isolation():
    sid = identity("one")
    other = identity("two")
    saved = artifacts.store_text(sid, "log", "é" * 30000)
    cursor, parts = 0, []
    while True:
        page = artifacts.read(sid, saved["id"], cursor)
        parts.append(page["text"])
        if page["next_cursor"] is None:
            break
        assert page["next_cursor"] > cursor
        cursor = page["next_cursor"]
    assert "".join(parts) == "é" * 30000
    with pytest.raises(ValueError, match="Unknown"):
        artifacts.read(other, saved["id"])


def test_snapshot_survives_rename_and_is_removed_with_session():
    sid = identity("one")
    saved = artifacts.store_text(sid, "report", "observed\nerror here\nfinished")
    session_store.rename_session("one", "renamed", [])
    assert artifacts.search(sid, saved["id"], "error")[0]["line"] == 2
    session_store.delete_session("renamed")
    with pytest.raises(ValueError):
        artifacts.read(sid, saved["id"])


def test_bound_tool_contract_and_partial_capture(monkeypatch):
    import json
    from vegapunk.tools import ALL_TOOLS
    from vegapunk.tools.artifacts import bind

    sid = identity("one")
    monkeypatch.setattr(artifacts, "CAPTURE_BYTES", 5)
    tools = {t.name: t for t in bind(ALL_TOOLS, lambda: sid)}
    saved = json.loads(tools["register_artifact"](title="unicode", text="ééé"))
    assert saved["complete"] == 0
    assert saved["captured_bytes"] == 4
    assert saved["size_bytes"] == 6
    assert json.loads(tools["read_artifact"](artifact_id=saved["id"]))["text"] == "éé"
    with pytest.raises(ValueError):
        artifacts.read(sid, saved["id"], -1)


@pytest.mark.parametrize("length", [0, 12000, 16384, 32768])
def test_exact_boundaries_and_empty_snapshots(length):
    sid = identity("one")
    saved = artifacts.store_text(sid, "boundary", "x" * length)
    cursor, total = 0, 0
    while True:
        page = artifacts.read(sid, saved["id"], cursor)
        total += len(page["text"])
        if page["next_cursor"] is None:
            break
        cursor = page["next_cursor"]
    assert total == length


def test_artifacts_cli_reads_current_session_snapshot():
    import json
    from vegapunk.commands import CommandContext, dispatch

    sid = identity("one")
    saved = artifacts.store_text(sid, "report", "evidence")
    ctx = CommandContext(session=None, current_name="one")
    assert json.loads(dispatch(f"/artifacts read {saved['id']}", ctx).output)["text"] == "evidence"
    assert "Could not read" in dispatch("/artifacts read missing", ctx).output
