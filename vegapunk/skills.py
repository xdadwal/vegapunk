"""Skills — reusable procedures Vegapunk pulls in on demand.

Vegapunk follows the Agent Skills format (https://agentskills.io): a skill is
a directory under ``.agents/skills/`` containing a ``SKILL.md`` — YAML-style
frontmatter over a markdown body — plus whatever ``scripts/``, ``references/``
or ``assets/`` the body points at. The format is tool-agnostic: a skill
written for any spec-following agent drops in here unchanged, and skills
written here work elsewhere.

The design is progressive disclosure at miniature scale: each skill costs the
system prompt only a one-line ``name — description`` ad; the full body enters
context only when the model calls ``use_skill`` (or the user stages one with
``/skill``). That keeps a shelf of skills nearly free until the moment one is
actually needed.

Vegapunk consumes the spec leniently, per the house loud-degradation rule: a
skill's identity is its DIRECTORY name, validated against the spec's naming
rules — content can't spoof it; a frontmatter ``name`` that disagrees earns a
stderr note and the directory wins; a missing ``description`` falls back to
the first body line; unknown frontmatter keys (``license``, ``metadata``,
``allowed-tools``, …) are ignored for forward compatibility. Skipping is the
exception, and always announced on stderr: spec-invalid directory names,
directories without a SKILL.md, empty bodies, unreadable manifests — and
legacy flat ``*.md`` skills, which get a migration nudge.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import config
from .session_store import slugify

# Advertised descriptions ride the system prompt every turn — keep them short.
# (The spec allows up to 1024 chars; the ad is Vegapunk's own display, capped.)
_DESCRIPTION_CAP = 100

# The spec's naming rule: 1-64 chars of lowercase alphanumerics joined by
# single hyphens — no edge hyphens, no doubles, nothing else.
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _valid_name(name: str) -> bool:
    return len(name) <= 64 and _NAME_RE.match(name) is not None


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
    name: str  # the directory name — the model-facing id
    description: str  # one normalized line, for the system-prompt ad
    path: Path  # the SKILL.md manifest

    @property
    def root(self) -> Path:
        """The skill's directory — what its relative file references resolve against."""
        return self.path.parent


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
    """When a manifest has no usable frontmatter description: its first
    non-empty line, heading markers stripped — a title is a serviceable ad."""
    for line in body.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return _normalize_description(stripped)
    return ""


def _parse(text: str, origin: Path) -> tuple[str, str, str]:
    """Split a SKILL.md into ``(frontmatter name, description, body)``.

    Frontmatter is a minimal hand-parsed ``---`` block (no YAML dependency):
    it exists iff line 1 is exactly ``---``; inside, only TOP-LEVEL lines (no
    leading whitespace) split on the first ``:``, and only ``name`` and
    ``description`` are honored — indented lines (a ``metadata:`` block's
    members, list items) and unknown keys (``license``, ``allowed-tools``, …)
    are ignored, for forward compatibility with the rest of the Agent Skills
    spec. Every malformed shape degrades to something usable instead of
    silently dropping a skill someone wrote: no fence or no description → the
    first body line becomes the ad; an unclosed fence is called out on stderr
    and the whole file is treated as body.
    """
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        body = text.strip()
        return "", _fallback_description(body), body

    fields: dict[str, str] = {}
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip() == "---":
            body = "\n".join(lines[i + 1 :]).strip()
            description = fields.get("description") or _fallback_description(body)
            return fields.get("name", ""), description, body
        if not line or line[0].isspace():
            continue  # a nested block's members are not top-level keys
        key, _, value = line.partition(":")
        key = key.strip()
        if key not in ("name", "description"):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1].strip()  # users copy YAML quoting habits
        fields[key] = _normalize_description(value) if key == "description" else value

    print(
        f"  [skills] {origin.parent.name}/{origin.name}: unclosed frontmatter; "
        "treating whole file as the skill body",
        file=sys.stderr,
    )
    body = text.strip()
    return "", _fallback_description(body), body


def list_skills() -> list[Skill]:
    """Discover skills: sorted subdirectories of ``skills_dir()`` that hold a
    ``SKILL.md``.

    Missing directory → ``[]`` (never created here — that's the user's move).
    Never raises: spec-invalid names, missing manifests, unreadable and empty
    manifests are skipped with a stderr note, and a legacy flat ``*.md`` skill
    gets a migration nudge; anything else (stray files, hidden directories) is
    ignored silently. Directory names are unique by construction, so there is
    no duplicate-name case to resolve.
    """
    directory = skills_dir()
    if not directory.is_dir():
        return []
    found: list[Skill] = []
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.suffix == ".md":
            stem = slugify(path.stem) or "skill-name"
            print(
                f"  [skills] {path.name}: flat skill files are no longer read — "
                f"move it to {stem}/SKILL.md (Agent Skills format)",
                file=sys.stderr,
            )
            continue
        if not path.is_dir() or path.name.startswith("."):
            continue  # a stray file or hidden directory isn't a skill, nor an error
        name = path.name
        if not _valid_name(name):
            print(
                f"  [skills] {name}/: not a valid skill name (lowercase letters, "
                "digits, single hyphens, max 64); skipped",
                file=sys.stderr,
            )
            continue
        manifest = path / "SKILL.md"
        if not manifest.is_file():
            print(f"  [skills] {name}/: no SKILL.md; skipped", file=sys.stderr)
            continue
        try:
            # utf-8-sig: tolerate a BOM (Windows Notepad and friends write
            # one); plain utf-8 would leak it into line 1 and break fence
            # detection — silently, which our loud-degradation rule forbids.
            text = manifest.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"  [skills] could not read {name}/SKILL.md: {exc}; skipped", file=sys.stderr)
            continue
        manifest_name, description, body = _parse(text, manifest)
        if not body:
            print(f"  [skills] {name}/SKILL.md: empty skill body; skipped", file=sys.stderr)
            continue
        if manifest_name and manifest_name != name:
            print(
                f"  [skills] {name}/SKILL.md says name '{manifest_name}' but the "
                f"directory is '{name}'; using the directory name",
                file=sys.stderr,
            )
        found.append(Skill(name=name, description=description, path=manifest))
    return found


def load_skill(name: str) -> tuple[Skill, str]:
    """Resolve ``name`` and return the matched ``Skill`` with its full body.

    Matching is forgiving for a small model and a hurried human: the slugified
    name matches exactly, or a substring that matches exactly ONE skill name
    resolves to it ("commit" finds "commit-message"). Ambiguous or unknown
    names raise ``SkillNotFound``. Lookup goes only through discovered paths —
    never a path built from the input — so traversal is impossible by
    construction.
    """
    skills = list_skills()
    names = [s.name for s in skills]
    # max_len matches the spec's 64-char name ceiling — slugify's default (40)
    # would make a long-but-valid advertised name impossible to exact-match.
    wanted = slugify(name, max_len=64)
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
    _name, _description, body = _parse(text, match.path)
    return match, body


def file_reference_note(skill: Skill) -> str:
    """A one-line pointer to the skill's bundled files, or ``""`` when it has
    none.

    The spec lets a body reference ``scripts/``, ``references/`` and
    ``assets/`` by paths relative to the skill root; the model resolves those
    with its file tools, so it needs to know where the skill lives — but only
    when there is actually something there to read.
    """
    try:
        # Hidden entries (.DS_Store and friends) aren't bundled files — same
        # posture as discovery, which ignores hidden directories.
        has_extras = any(
            p.name != "SKILL.md" and not p.name.startswith(".") for p in skill.root.iterdir()
        )
    except OSError:  # the directory vanished mid-turn; the body still stands alone
        return ""
    if not has_extras:
        return ""
    return (
        f"(Files this skill references live under {skill.root} — resolve its "
        "relative paths from there.)"
    )


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
