"""Tests for the grep tool — confined to a tmp workspace, deterministic.

Like the other filesystem tools, the workspace root is read live via
``workspace.workspace_root()``, so we monkeypatch that helper.
"""

from __future__ import annotations

import pytest

from vegapunk.tools.grep import grep


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr("vegapunk.tools.workspace.workspace_root", lambda: root)
    return root


def test_finds_match_with_path_and_lineno(ws):
    (ws / "a.txt").write_text("alpha\nbeta\ngamma\n")
    assert "a.txt:2: beta" in grep("beta")


def test_searches_multiple_files(ws):
    (ws / "a.txt").write_text("find me here\n")
    (ws / "b.txt").write_text("nothing\nfind me too\n")
    out = grep("find me")
    assert "a.txt:1: find me here" in out
    assert "b.txt:2: find me too" in out


def test_ignore_case(ws):
    (ws / "a.txt").write_text("Hello World\n")
    assert "a.txt:1: Hello World" in grep("hello", ignore_case=True)
    assert grep("hello") == "No matches for 'hello'."  # case-sensitive by default


def test_regex_pattern(ws):
    (ws / "code.py").write_text("def foo():\n    return 1\ndef bar():\n")
    out = grep(r"def \w+\(")
    assert "code.py:1: def foo():" in out
    assert "code.py:3: def bar():" in out


def test_preserves_leading_indentation(ws):
    (ws / "code.py").write_text("def foo():\n    return 42\n")
    assert "code.py:2:     return 42" in grep("return")  # indent kept for code context


def test_no_matches_returns_clear_string(ws):
    (ws / "a.txt").write_text("hello\n")
    assert grep("zzz") == "No matches for 'zzz'."


def test_invalid_regex_returns_clear_string(ws):
    (ws / "a.txt").write_text("hello\n")
    assert grep("(unclosed").startswith("Invalid pattern")


def test_search_within_subdir(ws):
    (ws / "src").mkdir()
    (ws / "src" / "x.py").write_text("needle\n")
    (ws / "y.py").write_text("needle\n")
    out = grep("needle", path="src")
    assert "src/x.py:1: needle" in out
    assert "y.py" not in out  # the root-level file is outside the searched subdir


def test_prunes_noise_dirs(ws):
    (ws / ".git").mkdir()
    (ws / ".git" / "config").write_text("secret needle\n")
    (ws / "real.txt").write_text("real needle\n")
    out = grep("needle")
    assert "real.txt:1: real needle" in out
    assert ".git" not in out  # pruned, never descended into


def test_skips_binary_files(ws):
    (ws / "bin.dat").write_bytes(b"\x00\x01 needle \xff\xfe")
    (ws / "text.txt").write_text("needle\n")
    out = grep("needle")
    assert "text.txt:1: needle" in out
    assert "bin.dat" not in out  # undecodable file skipped, no crash


def test_rejects_traversal(ws):
    assert grep("x", path="../../etc").startswith("Refused:")


def test_missing_path_returns_clear_string(ws):
    assert grep("x", path="nodir") == "No file or directory at 'nodir'."


def test_search_names_matches_path_not_content(ws):
    (ws / "alpha.py").write_text("nothing relevant\n")
    (ws / "beta.txt").write_text("nothing relevant\n")
    # Matches by file name; returns the bare path, no content lines.
    assert grep("alpha", target="names") == "alpha.py"


def test_search_names_by_extension(ws):
    (ws / "a.py").write_text("x\n")
    (ws / "b.py").write_text("x\n")
    (ws / "c.txt").write_text("x\n")
    out = grep(r"\.py$", target="names")
    assert "a.py" in out and "b.py" in out
    assert "c.txt" not in out


def test_search_names_finds_binary_by_name(ws):
    # Name search never reads contents, so it surfaces binary files too.
    (ws / "data.bin").write_bytes(b"\x00\xff")
    assert grep("data", target="names") == "data.bin"


def test_search_names_prunes_noise_dirs(ws):
    (ws / ".git").mkdir()
    (ws / ".git" / "needle.txt").write_text("x\n")
    (ws / "needle.txt").write_text("x\n")
    out = grep("needle", target="names")
    assert "needle.txt" in out.splitlines()
    assert ".git" not in out


def test_target_both_returns_names_and_content(ws):
    (ws / "needle.txt").write_text("has a needle inside\n")
    lines = grep("needle", target="both").splitlines()
    assert "needle.txt" in lines  # name-match line (bare path)
    assert any(line.startswith("needle.txt:1:") for line in lines)  # content-match line


def test_invalid_target_returns_clear_string(ws):
    (ws / "a.txt").write_text("x\n")
    assert grep("x", target="bogus").startswith("Invalid target")


def test_search_names_is_case_insensitive(ws):
    # The reported bug: lowercase "prompt" must find PROMPT.md.
    (ws / "PROMPT.md").write_text("the prompt\n")
    assert grep("prompt", target="names") == "PROMPT.md"


def test_search_names_loose_phrase_matches_any_word(ws):
    # "prompt file" should still surface PROMPT.md (matches the word "prompt").
    (ws / "PROMPT.md").write_text("x\n")
    (ws / "notes.txt").write_text("x\n")
    assert "PROMPT.md" in grep("prompt file", target="names").splitlines()


def test_content_search_stays_case_sensitive_by_default(ws):
    # Names go case-insensitive, but content keeps grep's precise default.
    (ws / "a.txt").write_text("Prompt here\n")
    assert grep("prompt", target="content") == "No matches for 'prompt'."
    assert "a.txt:1: Prompt here" in grep("prompt", target="content", ignore_case=True)
