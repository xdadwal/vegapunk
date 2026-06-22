"""A read-only tool that searches file contents in the workspace, like grep.

Reads only, so it isn't guarded; like the other filesystem tools it is confined
to the workspace root via ``workspace.resolve_in_workspace``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..config import config
from .registry import tool
from .workspace import PathOutsideWorkspace, resolve_in_workspace

# Noise directories — pruned from the walk so we don't descend into huge trees.
_SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".vegapunk"}

# Keep any single matched line readable; total output is capped separately.
_MAX_LINE = 200

_TARGETS = ("content", "names", "both")


@tool
def grep(pattern: str, path: str = ".", ignore_case: bool = False, target: str = "content") -> str:
    """Search the workspace for a pattern. With target="content" (default) it
    searches file CONTENTS like grep — case-sensitive unless ignore_case=True —
    and returns `relpath:lineno: line`. With target="names" it finds FILES by
    path, matching case-insensitively and partially, so "prompt" finds
    PROMPT.md; a loose multi-word query like "prompt file" matches files
    containing ANY of the words. Use target="names" to locate a file by part of
    its name, "content" to find where text/code appears, or "both". `path`
    (relative to the workspace) narrows the search; defaults to the workspace."""
    if target not in _TARGETS:
        return f"Invalid target {target!r}; use 'content', 'names', or 'both'."
    try:
        root = resolve_in_workspace(path)
        base = resolve_in_workspace(".")
    except PathOutsideWorkspace as exc:
        return f"Refused: {exc}"
    if not root.exists():
        return f"No file or directory at {path!r}."

    try:
        content_re = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        name_re = _name_regex(pattern)
    except re.error as exc:
        return f"Invalid pattern {pattern!r}: {exc}"

    files = [root] if root.is_file() else list(_walk(root))

    results: list[str] = []
    if target in ("names", "both"):
        results.extend(_match_names(files, name_re, base))
    if target in ("content", "both"):
        results.extend(_match_content(files, content_re, base))

    if not results:
        return (
            f"No matches for {pattern!r}. If you expected one, try ignore_case=true, "
            f'a shorter pattern, or target="names" to find files by name — or '
            f"list_dir to see what's here."
        )

    output = "\n".join(results)
    if len(output) > config.output_char_cap:
        output = output[: config.output_char_cap] + "\n...[truncated]"
    return output


def _name_regex(pattern: str) -> re.Pattern:
    """A forgiving matcher for file *names*: always case-insensitive, and a
    multi-word query matches a path containing ANY of the words — so a loose
    phrase like "prompt file" still finds PROMPT.md. A single token is used
    as-is, so it still works as a regex (e.g. ``\\.py$``)."""
    tokens = pattern.split()
    body = "|".join(re.escape(token) for token in tokens) if len(tokens) > 1 else pattern
    return re.compile(body, re.IGNORECASE)


def _match_names(files: list[Path], regex: re.Pattern, base: Path) -> list[str]:
    """File paths (relative to the workspace) whose path matches the pattern."""
    out = []
    for file in files:
        rel = str(file.relative_to(base))
        if regex.search(rel):
            out.append(rel)
    return out


def _match_content(files: list[Path], regex: re.Pattern, base: Path) -> list[str]:
    """Matching lines as `relpath:lineno: line`, skipping unreadable files."""
    out = []
    for file in files:
        try:
            text = file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # skip binary / unreadable files
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                line = line.rstrip()
                if len(line) > _MAX_LINE:
                    line = line[:_MAX_LINE] + "..."
                out.append(f"{file.relative_to(base)}:{lineno}: {line}")
    return out


def _walk(root: Path):
    """Yield files under ``root``, pruning noise directories so we don't descend
    into large trees like .git / .venv / node_modules."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in sorted(filenames):
            yield Path(dirpath) / name
