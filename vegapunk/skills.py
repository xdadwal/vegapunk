"""Skills — user-written guides Vegapunk pulls in on demand.

A skill is one markdown file under ``.vegapunk/skills/`` teaching the agent a
repeatable procedure ("how to write a commit message for this repo"). The
design is progressive disclosure at miniature scale: each skill costs the
system prompt only a one-line ``name — description`` ad; the full body enters
context only when the model calls ``use_skill`` (or the user stages one with
``/skill``). That keeps a shelf of skills nearly free until the moment one is
actually needed.

The skill's name is its slugified filename stem, so what's advertised is
always a safe token the model can echo back verbatim. Files are read
defensively (the ``memory.load_memory`` posture): a malformed file degrades
loudly to something usable rather than being silently ignored. Skipping is
the exception, and always announced on stderr: empty files (nothing to
teach), unreadable ones, duplicate names, and names with no usable
characters.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .config import config
from .session_store import slugify

# Advertised descriptions ride the system prompt every turn — keep them short.
_DESCRIPTION_CAP = 100


class SkillNotFound(Exception):
    """No skill by that name (or the name is ambiguous).

    Carries the names that were available, so error paths can tell the model
    (or the user) what to try instead without a second discovery pass — which
    would re-print any malformed-file notes.
    """

    def __init__(self, name: str, available: list[str] | None = None) -> None:
        super().__init__(name)
        self.available = available or []


@dataclass(frozen=True)
class Skill:
    name: str  # slugified filename stem — the model-facing id
    description: str  # one normalized line, for the system-prompt ad
    path: Path


def skills_dir() -> Path:
    """The directory skills live in (a function so tests monkeypatch it,
    mirroring ``session_store.sessions_dir``)."""
    return config.skills_dir


def _normalize_description(text: str) -> str:
    """One line, whitespace collapsed, capped — ad-sized."""
    collapsed = " ".join(text.split())
    if len(collapsed) > _DESCRIPTION_CAP:
        collapsed = collapsed[: _DESCRIPTION_CAP - 1] + "…"
    return collapsed


def _fallback_description(body: str) -> str:
    """When a file has no usable frontmatter description: its first non-empty
    line, heading markers stripped — a title is a serviceable ad."""
    for line in body.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return _normalize_description(stripped)
    return ""


def _parse(text: str, origin: Path) -> tuple[str, str]:
    """Split a skill file into ``(description, body)``.

    Frontmatter is a minimal hand-parsed ``---`` block (no YAML dependency):
    it exists iff line 1 is exactly ``---``; inside, lines split on the first
    ``:`` and only ``description`` is honored (unknown keys are ignored, for
    forward compatibility). Every malformed shape degrades to something usable
    instead of silently dropping a file the user wrote: no fence or no
    description → the first body line becomes the ad; an unclosed fence is
    called out on stderr and the whole file is treated as body.
    """
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        body = text.strip()
        return _fallback_description(body), body

    description = ""
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip() == "---":
            body = "\n".join(lines[i + 1 :]).strip()
            if description:
                return description, body
            return _fallback_description(body), body
        key, _, value = line.partition(":")
        if key.strip() == "description":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1].strip()  # users copy YAML quoting habits
            description = _normalize_description(value)

    print(
        f"  [skills] {origin.name}: unclosed frontmatter; treating whole file as the skill body",
        file=sys.stderr,
    )
    body = text.strip()
    return _fallback_description(body), body


def list_skills() -> list[Skill]:
    """Discover skills: sorted ``*.md`` under ``skills_dir()``.

    Missing directory → ``[]`` (never created here — that's the user's move).
    Never raises: unreadable and empty files are skipped with a stderr note,
    duplicate slugs keep the first file in sorted order, and non-``.md`` files
    are ignored silently (a stray ``.md~`` backup isn't an error).
    """
    directory = skills_dir()
    if not directory.is_dir():
        return []
    found: list[Skill] = []
    seen: dict[str, Path] = {}
    for path in sorted(directory.glob("*.md")):
        name = slugify(path.stem)
        if not name:
            print(f"  [skills] {path.name}: name has no usable characters; skipped", file=sys.stderr)
            continue
        if name in seen:
            print(
                f"  [skills] {path.name}: name clashes with {seen[name].name}; skipped",
                file=sys.stderr,
            )
            continue
        try:
            # utf-8-sig: tolerate a BOM (Windows Notepad and friends write
            # one); plain utf-8 would leak it into line 1 and break fence
            # detection — silently, which our loud-degradation rule forbids.
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"  [skills] could not read {path.name}: {exc}; skipped", file=sys.stderr)
            continue
        description, body = _parse(text, path)
        if not body:
            print(f"  [skills] {path.name}: empty skill body; skipped", file=sys.stderr)
            continue
        seen[name] = path
        found.append(Skill(name=name, description=description, path=path))
    return found


def load_skill(name: str) -> tuple[str, str]:
    """Resolve ``name`` and return ``(canonical_name, body)``.

    Matching is forgiving for a small model and a hurried human: the slugified
    name matches exactly, or a substring that matches exactly ONE skill name
    resolves to it ("commit" finds "commit-message"). Ambiguous or unknown
    names raise ``SkillNotFound``. Lookup goes only through discovered paths —
    never a path built from the input — so traversal is impossible by
    construction.
    """
    skills = list_skills()
    names = [s.name for s in skills]
    wanted = slugify(name)
    match = next((s for s in skills if s.name == wanted), None)
    if match is None and wanted:
        partial = [s for s in skills if wanted in s.name]
        if len(partial) == 1:
            match = partial[0]
    if match is None:
        raise SkillNotFound(name, names)
    try:
        # utf-8-sig: tolerate a BOM (Windows Notepad and friends write one);
        # plain utf-8 would leak it into line 1 and break fence detection.
        text = match.path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:  # vanished/corrupted since discovery
        raise SkillNotFound(name, names) from exc
    _description, body = _parse(text, match.path)
    return match.name, body


def as_system_block() -> str:
    """The skills stanza for the system prompt, or ``""`` when none exist.

    One line per skill — the cheap half of progressive disclosure; the body
    stays on disk until use_skill pulls it in.
    """
    skills = list_skills()
    if not skills:
        return ""
    return (
        "\n\nSkills you have — user-written guides for specific tasks. When a "
        "task matches one, call use_skill with its name first and follow what "
        "it returns:\n" + "\n".join(f"- {s.name} — {s.description}" for s in skills)
    )
