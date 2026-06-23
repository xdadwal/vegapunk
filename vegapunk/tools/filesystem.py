"""Filesystem tools, confined to the Vegapunk workspace root.

Changing a file on disk is guarded, so ``write_file`` (whole-file) and
``edit_file`` (targeted string replacement) both go through the approval gate;
reading and listing are safe and run freely. Every path is validated against the
workspace root (see ``workspace.resolve_in_workspace``), so the model can't reach
files outside it.
"""

from __future__ import annotations

from .registry import tool
from .workspace import PathOutsideWorkspace, resolve_in_workspace


@tool(guarded=True)
def write_file(path: str, content: str) -> str:
    """Create or OVERWRITE a text file with the given content. Call this to save a
    new file or replace a whole one; it replaces any existing file at `path`. To
    change only part of an existing file, use edit_file instead of rewriting it
    all. `path` is relative to the workspace. Returns what was written so you can
    confirm it."""
    try:
        target = resolve_in_workspace(path)
    except PathOutsideWorkspace as exc:
        return f"Refused: {exc}"
    target.parent.mkdir(parents=True, exist_ok=True)
    written = target.write_text(content, encoding="utf-8")
    return f"Wrote {written} characters to {target}."


@tool(guarded=True)
def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Make a targeted edit to an existing workspace file by replacing an exact
    snippet — use this instead of write_file when you only need to change part of
    a file, so you don't rewrite the whole thing. Read the file first and copy
    `old_string` verbatim (including indentation and enough surrounding lines to
    match exactly one place), or set `replace_all` to change every occurrence.
    `path` is relative to the workspace."""
    try:
        target = resolve_in_workspace(path)
    except PathOutsideWorkspace as exc:
        return f"Refused: {exc}"
    if not target.is_file():
        return (
            f"No file at {path!r}. edit_file changes an existing file — use write_file "
            f"to create a new one, or list_dir/grep to find the correct path."
        )
    if old_string == "":
        return "old_string must not be empty — provide the exact text to replace."
    if old_string == new_string:
        return "old_string and new_string are identical — nothing to change."
    text = target.read_text(encoding="utf-8")
    count = text.count(old_string)
    if count == 0:
        return (
            "old_string not found in the file. Read the file again and copy the exact "
            "text to replace, including indentation and whitespace, then retry."
        )
    if count > 1 and not replace_all:
        return (
            f"old_string matches {count} places, so the edit is ambiguous. Include more "
            f"surrounding context to make it unique, or set replace_all=true to change all {count}."
        )
    written = target.write_text(text.replace(old_string, new_string), encoding="utf-8")
    plural = "s" if count != 1 else ""
    return f"Edited {target}: replaced {count} occurrence{plural}; the file is now {written} characters."


@tool
def read_file(path: str) -> str:
    """Read and return the full text of a file in the workspace. Call this
    BEFORE editing or overwriting a file so you know its current contents.
    `path` is relative to the workspace."""
    try:
        target = resolve_in_workspace(path)
    except PathOutsideWorkspace as exc:
        return f"Refused: {exc}"
    if not target.is_file():
        return (
            f"No file at {path!r}. It may be named differently. Do not reply to "
            f"the user yet — call list_dir or grep now to find the correct name, "
            f"then read that path."
        )
    return target.read_text(encoding="utf-8")


@tool
def list_dir(path: str = ".") -> str:
    """List the entries in a workspace directory (defaults to the workspace
    root). Call this to discover what files exist before reading or writing.
    Directories are suffixed with '/'. `path` is relative to the workspace."""
    try:
        target = resolve_in_workspace(path)
    except PathOutsideWorkspace as exc:
        return f"Refused: {exc}"
    if not target.is_dir():
        return (
            f"No directory at {path!r}. Do not reply to the user yet — call "
            f"list_dir on '.' to see the workspace, then use a path that exists."
        )
    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
    return "\n".join(entries) if entries else "(empty)"
