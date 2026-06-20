"""Filesystem tools, confined to the Vegapunk workspace root.

Writing a file changes the disk, so ``write_file`` is guarded and goes through
the approval gate; reading and listing are safe and run freely. Every path is
validated against the workspace root (see ``workspace.resolve_in_workspace``),
so the model can't reach files outside it.
"""

from __future__ import annotations

from .registry import tool
from .workspace import PathOutsideWorkspace, resolve_in_workspace


@tool(guarded=True)
def write_file(path: str, content: str) -> str:
    """Create or OVERWRITE a text file with the given content. Call this only to
    save or replace a whole file; it replaces any existing file at `path`. Read
    the file first if you need to preserve its current contents. `path` is
    relative to the workspace. Returns what was written so you can confirm it."""
    try:
        target = resolve_in_workspace(path)
    except PathOutsideWorkspace as exc:
        return f"Refused: {exc}"
    target.parent.mkdir(parents=True, exist_ok=True)
    written = target.write_text(content, encoding="utf-8")
    return f"Wrote {written} characters to {target}."


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
        return f"No file at {path!r}."
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
        return f"No directory at {path!r}."
    entries = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
    return "\n".join(entries) if entries else "(empty)"
