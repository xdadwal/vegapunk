"""A guarded tool that runs a shell command in the workspace.

This is powerful by design — arbitrary commands — so it is guarded: the
approval gate is its safety net, not a denylist. It runs in the workspace root,
times out, and truncates very long output to protect the context window.
"""

from __future__ import annotations

import subprocess

from ..config import config
from .registry import tool
from .workspace import workspace_root

# Module-level seam: tests patch this to inject results without spawning.
_run = subprocess.run


@tool(guarded=True)
def run_shell(command: str) -> str:
    """Run a shell command and return its combined stdout+stderr. Use this for
    things the other tools can't do — build, test, inspect, git, and so on. It
    runs in the workspace with a time limit, and very long output is truncated.
    The exit code is reported in the result so you can tell success from failure."""
    try:
        completed = _run(
            command,
            shell=True,
            cwd=str(workspace_root()),
            capture_output=True,
            text=True,
            timeout=config.shell_timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Timed out after {config.shell_timeout:g}s: {command!r}"

    output = (completed.stdout or "") + (completed.stderr or "")
    if len(output) > config.output_char_cap:
        output = output[: config.output_char_cap] + "\n...[truncated]"
    status = "exit 0" if completed.returncode == 0 else f"exit {completed.returncode}"
    body = output.strip() or "(no output)"
    return f"[{status}]\n{body}"
