"""Tests for the ``use_skill`` tool — deterministic, no model/network.

These pin the tool-result contract the model steers by: an imperatively framed
body on success, a corrective listing on an unknown name (so a wrong guess
becomes a retry, not a dead end), the house truncation cap for unbounded
user-authored files, and the bundled-files pointer for skills that ship
``scripts/``/``references/``/``assets/`` per the Agent Skills format.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from vegapunk.config import config
from vegapunk.tools import ALL_TOOLS
from vegapunk.tools.skills import use_skill


@pytest.fixture
def skills_home(tmp_path, monkeypatch):
    monkeypatch.setattr("vegapunk.skills.skills_dir", lambda: tmp_path)
    return tmp_path


def _write_skill(home, name, text):
    """A skill in the Agent Skills layout: <name>/SKILL.md."""
    (home / name).mkdir(parents=True, exist_ok=True)
    (home / name / "SKILL.md").write_text(text, encoding="utf-8")


def test_success_frames_the_body_imperatively(skills_home):
    _write_skill(
        skills_home, "commit-message", "---\ndescription: d\n---\nUse type(scope): summary."
    )
    result = use_skill("commit-message")
    assert result.startswith("Skill 'commit-message' loaded. Follow these instructions")
    assert "Use type(scope): summary." in result


def test_forgiving_match_reports_the_canonical_name(skills_home):
    _write_skill(skills_home, "commit-message", "body text")
    result = use_skill("commit")
    assert "Skill 'commit-message' loaded" in result  # partial match, canonical echo


def test_unknown_name_lists_available_skills(skills_home):
    _write_skill(skills_home, "commit-message", "a")
    _write_skill(skills_home, "weekly-review", "b")
    result = use_skill("deploy")
    assert "No skill named 'deploy'" in result
    assert "commit-message" in result and "weekly-review" in result  # every option named
    assert "use_skill" in result  # tells the model how to recover


def test_no_skills_installed_says_so_and_frees_the_model(skills_home):
    result = use_skill("anything")
    assert "No skills are installed" in result
    assert "own judgment" in result  # explicitly unblocks the task
    assert str(skills_home) in result  # tells the human where skills go
    assert "SKILL.md" in result  # ...and in what format (Agent Skills)


def test_long_body_is_truncated_with_the_house_marker(skills_home, monkeypatch):
    _write_skill(skills_home, "big", "x" * 500)
    monkeypatch.setattr("vegapunk.tools.skills.config", replace(config, output_char_cap=50))
    result = use_skill("big")
    assert result.endswith("...[truncated]")
    assert "x" * 51 not in result


def test_bundled_files_note_points_at_the_skill_root(skills_home):
    _write_skill(skills_home, "bundled", "Run scripts/go.py to start.")
    (skills_home / "bundled" / "scripts").mkdir()
    (skills_home / "bundled" / "scripts" / "go.py").write_text("print('hi')", encoding="utf-8")
    result = use_skill("bundled")
    assert str(skills_home / "bundled") in result  # relative refs resolve from here


def test_bundled_files_note_survives_truncation(skills_home, monkeypatch):
    # The pointer must outlive the cap — a truncated body that references
    # scripts/ is exactly when the model needs to know where they live.
    _write_skill(skills_home, "bundled", "x" * 500)
    (skills_home / "bundled" / "scripts").mkdir()
    (skills_home / "bundled" / "scripts" / "go.py").write_text("", encoding="utf-8")
    monkeypatch.setattr("vegapunk.tools.skills.config", replace(config, output_char_cap=50))
    result = use_skill("bundled")
    assert "...[truncated]" in result
    assert str(skills_home / "bundled") in result


def test_manifest_only_skill_gets_no_files_note(skills_home):
    _write_skill(skills_home, "bare", "just instructions")
    result = use_skill("bare")
    assert "live under" not in result  # nothing bundled, nothing to point at


def test_registered_unguarded_with_required_name(skills_home):
    made = next(t for t in ALL_TOOLS if t.name == "use_skill")
    assert made.guarded is False  # read-only, no approval gate
    params = made.to_schema()["function"]["parameters"]
    assert params["properties"]["name"] == {"type": "string"}
    assert params["required"] == ["name"]


def test_empty_name_gets_the_corrective_listing(skills_home):
    _write_skill(skills_home, "commit-message", "body")
    result = use_skill("")
    assert "No skill named ''" in result
    assert "commit-message" in result


def test_unknown_name_prints_discovery_notes_once(skills_home, capsys):
    # One lookup = one discovery pass: the corrective listing comes from the
    # exception's carried names, so a malformed file's note isn't doubled.
    _write_skill(skills_home, "broken", "---\ndescription: never closed\ncontent")
    use_skill("nope")
    assert capsys.readouterr().err.count("unclosed frontmatter") == 1
