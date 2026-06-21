"""Tests for the shell tool — deterministic and fast.

The timeout test shrinks the timeout (not the wait), so a 5s sleep is killed in
a fraction of a second. ``config`` is frozen, so we swap in a modified copy with
``dataclasses.replace``.
"""

from __future__ import annotations

import sys
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


def test_stdin_is_fed_to_command(ws):
    out = run_shell("cat", stdin="hello\n")  # cat echoes whatever it reads on stdin
    assert "hello" in out
    assert "[exit 0]" in out


def test_default_stdin_closed_no_hang(ws, monkeypatch):
    # No stdin given: it's closed, so a reader gets EOF and exits at once rather
    # than blocking on the inherited terminal. The short timeout bounds a
    # regression (a real block would surface as "Timed out", not a hang).
    monkeypatch.setattr("vegapunk.tools.shell.config", replace(config, shell_timeout=5))
    out = run_shell("cat")
    assert out.startswith("[exit 0]")
    assert not out.startswith("Timed out")


def test_interactive_prompt_fails_fast_not_timeout(ws, monkeypatch):
    # A prompt with no input used to hang until the timeout; now stdin is closed
    # so input() raises EOFError immediately. Bound a regression with a short
    # timeout that's still well clear of interpreter startup.
    monkeypatch.setattr("vegapunk.tools.shell.config", replace(config, shell_timeout=5))
    out = run_shell(f'{sys.executable} -c "input()"')
    assert not out.startswith("Timed out")
    assert "[exit 0]" not in out  # EOFError -> non-zero exit
    assert "EOFError" in out


def test_stdin_answers_prompt(ws):
    out = run_shell(f"{sys.executable} -c \"print('Hi ' + input())\"", stdin="Akshay\n")
    assert "Hi Akshay" in out
    assert "[exit 0]" in out


def test_stdin_feeds_multiple_prompts_in_order(ws):
    # A script that reads several inputs gets each answer in turn from the one
    # newline-separated stdin string — the common "fixed series of questions" case.
    code = "a=input(); b=input(); c=input(); print(a + '-' + b + '-' + c)"
    out = run_shell(f'{sys.executable} -c "{code}"', stdin="one\ntwo\nthree\n")
    assert "one-two-three" in out
    assert "[exit 0]" in out


def test_partial_stdin_runs_out_at_next_prompt(ws, monkeypatch):
    # Fewer answers than prompts: the supplied ones are consumed, then the next
    # read hits EOF and fails fast (what drives incremental prompt discovery).
    monkeypatch.setattr("vegapunk.tools.shell.config", replace(config, shell_timeout=5))
    code = "a=input(); b=input(); print('reached-end')"
    out = run_shell(f'{sys.executable} -c "{code}"', stdin="only-one\n")
    assert not out.startswith("Timed out")
    assert "EOFError" in out
    assert "reached-end" not in out
