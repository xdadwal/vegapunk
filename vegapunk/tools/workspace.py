"""Confine the filesystem and shell tools to a single workspace root.

Boundary validation lives here so every side-effecting tool resolves and checks
paths the same way. ``resolve_in_workspace`` raises on any escape (``..``, an
absolute path outside the root, or a symlink pointing out); the calling tool
turns that into a clear string the model can react to.
"""

from __future__ import annotations

from pathlib import Path

from ..config import config


class PathOutsideWorkspace(ValueError):
    """A requested path resolved outside the workspace root."""


def workspace_root() -> Path:
    """The fully resolved workspace root (symlinks in the root normalized too)."""
    return Path(config.workspace_root).resolve()


def resolve_in_workspace(path: str, *, root: Path | None = None) -> Path:
    """Resolve ``path`` against the workspace root, rejecting any escape.

    Fully resolves both sides (so ``..`` is collapsed and symlinks followed),
    then requires the result to be the root itself or contained under it. An
    absolute ``path`` overrides the root and is therefore rejected unless it
    already lives inside the workspace. Raises ``PathOutsideWorkspace`` on escape.
    """
    base = root.resolve() if root is not None else workspace_root()
    candidate = (base / path).resolve()
    if candidate != base and base not in candidate.parents:
        raise PathOutsideWorkspace(f"{path!r} resolves outside the workspace ({base}).")
    return candidate
