"""Tests for the shell tool — deterministic and fast.

The timeout test shrinks the timeout (not the wait), so a 5s sleep is killed in
a fraction of a second. ``config`` is frozen, so we swap in a modified copy with
``dataclasses.replace``.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from vegapunk.config import config
from vegapunk.tools.shell import run_shell


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.setattr("vegapunk.tools.shell.workspace_root", lambda: root)
    return root


def test_captures_stdout(ws):
    out = run_shell("echo hello")
    assert "hello" in out
    assert "[exit 0]" in out


def test_captures_stderr(ws):
    out = run_shell("echo oops 1>&2")
    assert "oops" in out  # stderr is merged into the combined output


def test_reports_nonzero_exit(ws):
    out = run_shell("exit 3")
    assert "[exit 3]" in out  # non-zero reported, not raised


def test_runs_in_workspace_cwd(ws):
    out = run_shell("pwd")
    assert str(ws) in out


def test_truncates_long_output(ws, monkeypatch):
    monkeypatch.setattr("vegapunk.tools.shell.config", replace(config, output_char_cap=10))
    out = run_shell("printf 'xxxxxxxxxxxxxxxxxxxx'")  # 20 chars > cap of 10
    assert out.endswith("...[truncated]")


def test_times_out_fast(ws, monkeypatch):
    monkeypatch.setattr("vegapunk.tools.shell.config", replace(config, shell_timeout=0.2))
    out = run_shell("sleep 5")
    assert out.startswith("Timed out after")
