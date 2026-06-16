"""Tests for the filesystem tools — confined to a tmp workspace.

The workspace root is read live via ``workspace.workspace_root()``; ``config``
is frozen, so we monkeypatch that helper rather than mutating config.
"""

from __future__ import annotations

import pytest

from vegapunk.tools.filesystem import list_dir, read_file, write_file


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr("vegapunk.tools.workspace.workspace_root", lambda: root)
    return root


def test_write_file_writes_within_root(ws):
    result = write_file("notes/a.txt", "hello")
    assert "5 characters" in result
    assert (ws / "notes/a.txt").read_text() == "hello"  # parent dir auto-created


def test_write_file_overwrites_existing(ws):
    write_file("a.txt", "first")
    write_file("a.txt", "second")
    assert (ws / "a.txt").read_text() == "second"  # overwrite, not append


def test_write_file_rejects_parent_traversal(ws):
    result = write_file("../escape.txt", "x")
    assert result.startswith("Refused:")
    assert not (ws.parent / "escape.txt").exists()  # nothing written


def test_write_file_rejects_absolute_outside_root(ws):
    result = write_file(str(ws.parent / "evil.txt"), "x")
    assert result.startswith("Refused:")
    assert not (ws.parent / "evil.txt").exists()


def test_write_file_allows_absolute_inside_root(ws):
    result = write_file(str(ws / "ok.txt"), "hi")
    assert "characters" in result
    assert (ws / "ok.txt").read_text() == "hi"


def test_read_file_happy_path(ws):
    (ws / "r.txt").write_text("contents here")
    assert read_file("r.txt") == "contents here"


def test_read_file_missing_returns_clear_string(ws):
    assert read_file("nope.txt") == "No file at 'nope.txt'."


def test_read_file_rejects_traversal(ws):
    assert read_file("../../etc/passwd").startswith("Refused:")


def test_list_dir_lists_entries(ws):
    (ws / "f.txt").write_text("x")
    (ws / "sub").mkdir()
    out = list_dir(".")
    assert "f.txt" in out
    assert "sub/" in out  # directories suffixed with '/'


def test_list_dir_missing_returns_clear_string(ws):
    assert list_dir("nodir") == "No directory at 'nodir'."
