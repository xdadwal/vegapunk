"""Tests for the filesystem tools — confined to a tmp workspace.

The workspace root is read live via ``workspace.workspace_root()``; ``config``
is frozen, so we monkeypatch that helper rather than mutating config.
"""

from __future__ import annotations

import pytest

from vegapunk.tools.filesystem import edit_file, list_dir, read_file, write_file


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
    out = read_file("nope.txt")
    assert out.startswith("No file at 'nope.txt'.")
    assert "list_dir" in out  # steers recovery instead of dead-ending


def test_read_file_rejects_traversal(ws):
    assert read_file("../../etc/passwd").startswith("Refused:")


def test_list_dir_lists_entries(ws):
    (ws / "f.txt").write_text("x")
    (ws / "sub").mkdir()
    out = list_dir(".")
    assert "f.txt" in out
    assert "sub/" in out  # directories suffixed with '/'


def test_list_dir_missing_returns_clear_string(ws):
    out = list_dir("nodir")
    assert out.startswith("No directory at 'nodir'.")
    assert "list_dir" in out  # steers recovery


def test_edit_file_replaces_unique_snippet(ws):
    (ws / "code.py").write_text("a = 1\nb = 2\nc = 3\n")

    result = edit_file("code.py", "b = 2", "b = 20")

    assert "1 occurrence" in result
    assert (ws / "code.py").read_text() == "a = 1\nb = 20\nc = 3\n"


def test_edit_file_not_found_leaves_file_unchanged(ws):
    (ws / "code.py").write_text("a = 1\n")

    result = edit_file("code.py", "does not exist", "x")

    assert "not found" in result
    assert (ws / "code.py").read_text() == "a = 1\n"  # untouched


def test_edit_file_ambiguous_match_is_refused_without_replace_all(ws):
    (ws / "code.py").write_text("x = 1\nx = 1\n")  # two identical lines

    result = edit_file("code.py", "x = 1", "x = 2")

    assert "matches 2 places" in result
    assert (ws / "code.py").read_text() == "x = 1\nx = 1\n"  # nothing changed


def test_edit_file_replace_all_changes_every_occurrence(ws):
    (ws / "code.py").write_text("x = 1\nx = 1\n")

    result = edit_file("code.py", "x = 1", "x = 2", replace_all=True)

    assert "2 occurrences" in result
    assert (ws / "code.py").read_text() == "x = 2\nx = 2\n"


def test_edit_file_replaces_multiline_block(ws):
    # The tool's primary use: match a multi-line snippet (indentation + newlines)
    # uniquely and swap it.
    (ws / "code.py").write_text("def f():\n    x = 1\n    return x\n")

    result = edit_file("code.py", "    x = 1\n    return x", "    x = 2\n    return x + 1")

    assert "1 occurrence" in result
    assert (ws / "code.py").read_text() == "def f():\n    x = 2\n    return x + 1\n"


def test_edit_file_missing_file_points_to_write_file(ws):
    out = edit_file("nope.txt", "a", "b")
    assert out.startswith("No file at 'nope.txt'.")
    assert "write_file" in out  # steers to the right tool


def test_edit_file_empty_old_string_is_rejected(ws):
    (ws / "code.py").write_text("a = 1\n")

    result = edit_file("code.py", "", "INSERTED")

    assert "must not be empty" in result
    assert (ws / "code.py").read_text() == "a = 1\n"  # not mangled


def test_edit_file_identical_strings_change_nothing(ws):
    (ws / "code.py").write_text("a = 1\n")

    result = edit_file("code.py", "a = 1", "a = 1")

    assert "identical" in result
    assert (ws / "code.py").read_text() == "a = 1\n"


def test_edit_file_rejects_traversal(ws):
    assert edit_file("../../etc/hosts", "a", "b").startswith("Refused:")
