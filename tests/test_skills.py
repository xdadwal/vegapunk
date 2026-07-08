"""Tests for the skills store — deterministic, no model/network.

These pin the parsing contract (rule by rule: frontmatter, fallbacks, the
loud-degrade cases) and the forgiving-but-safe name resolution. Discovery is
driven against a tmp_path via the same directory-function monkeypatch seam the
session-store tests use.
"""

from __future__ import annotations

import pytest

from vegapunk import skills
from vegapunk.skills import Skill, SkillNotFound, as_system_block, list_skills, load_skill


@pytest.fixture
def skills_home(tmp_path, monkeypatch):
    monkeypatch.setattr("vegapunk.skills.skills_dir", lambda: tmp_path)
    return tmp_path


def _write(home, filename, text):
    (home / filename).write_text(text, encoding="utf-8")


WELL_FORMED = """---
description: How to write a commit message for this repo
---
# Commit messages

- type(scope): summary
"""


def test_well_formed_skill_parses_name_description_and_body(skills_home):
    _write(skills_home, "commit-message.md", WELL_FORMED)
    (skill,) = list_skills()
    assert skill.name == "commit-message"  # slugified filename stem
    assert skill.description == "How to write a commit message for this repo"

    name, body = load_skill("commit-message")
    assert name == "commit-message"
    assert "- type(scope): summary" in body
    assert "---" not in body  # the fence block is not part of the body


def test_name_comes_from_filename_not_frontmatter(skills_home):
    _write(skills_home, "My Fancy Skill.md", WELL_FORMED)
    (skill,) = list_skills()
    assert skill.name == "my-fancy-skill"


def test_quoted_description_is_unquoted(skills_home):
    _write(skills_home, "a.md", '---\ndescription: "Quoted, like YAML"\n---\nbody')
    assert list_skills()[0].description == "Quoted, like YAML"


def test_unknown_frontmatter_keys_are_ignored(skills_home):
    _write(skills_home, "a.md", "---\nguarded: true\ndescription: Real one\nauthor: bob\n---\nbody")
    assert list_skills()[0].description == "Real one"


def test_no_frontmatter_falls_back_to_first_line_as_description(skills_home):
    _write(skills_home, "notes.md", "# Weekly review checklist\n\nDo the things.")
    (skill,) = list_skills()
    assert skill.description == "Weekly review checklist"  # heading markers stripped
    _, body = load_skill("notes")
    assert body.startswith("# Weekly review checklist")  # body keeps the whole file


def test_frontmatter_without_description_falls_back(skills_home):
    _write(skills_home, "a.md", "---\nauthor: bob\n---\nFirst real line\nmore")
    assert list_skills()[0].description == "First real line"


def test_long_description_is_collapsed_and_capped(skills_home):
    long = "words " * 40  # ~240 chars with internal runs
    _write(skills_home, "a.md", f"---\ndescription: {long}\n---\nbody")
    description = list_skills()[0].description
    assert len(description) == 100
    assert description.endswith("…")
    assert "\n" not in description


def test_unclosed_frontmatter_degrades_to_whole_file_with_a_note(skills_home, capsys):
    _write(skills_home, "broken.md", "---\ndescription: never closed\nThe actual content")
    (skill,) = list_skills()
    _, body = load_skill("broken")
    assert "The actual content" in body
    assert body.startswith("---")  # whole file treated as body — nothing hidden
    assert "unclosed frontmatter" in capsys.readouterr().err


def test_empty_skill_is_skipped_with_a_note(skills_home, capsys):
    _write(skills_home, "empty.md", "---\ndescription: all hat no cattle\n---\n   \n")
    _write(skills_home, "real.md", "content")
    assert [s.name for s in list_skills()] == ["real"]
    assert "empty skill body" in capsys.readouterr().err


def test_unreadable_file_is_skipped_and_siblings_survive(skills_home, capsys):
    (skills_home / "binary.md").write_bytes(b"\xff\xfe\x00 not utf-8 \xff")
    _write(skills_home, "good.md", "fine content")
    assert [s.name for s in list_skills()] == ["good"]
    assert "could not read binary.md" in capsys.readouterr().err


def test_duplicate_slugs_keep_first_in_sorted_order(skills_home, capsys):
    _write(skills_home, "My Skill.md", "from the spaced file")
    _write(skills_home, "my-skill.md", "from the dashed file")
    (skill,) = list_skills()
    # "My Skill.md" sorts before "my-skill.md" (capitals first in ASCII).
    assert skill.path.name == "My Skill.md"
    assert "clashes with" in capsys.readouterr().err


def test_unslugifiable_stem_is_skipped_with_a_note(skills_home, capsys):
    _write(skills_home, "!!!.md", "content")
    assert list_skills() == []
    assert "no usable characters" in capsys.readouterr().err


def test_non_md_files_are_ignored_silently(skills_home, capsys):
    _write(skills_home, "notes.txt", "not a skill")
    _write(skills_home, "skill.md~", "editor backup")
    assert list_skills() == []
    assert capsys.readouterr().err == ""  # not an error, not even a note


def test_missing_dir_lists_nothing_and_does_not_create_it(tmp_path, monkeypatch):
    ghost = tmp_path / "never-made"
    monkeypatch.setattr("vegapunk.skills.skills_dir", lambda: ghost)
    assert list_skills() == []
    assert not ghost.exists()


def test_load_skill_is_forgiving_about_case_and_spacing(skills_home):
    _write(skills_home, "commit-message.md", WELL_FORMED)
    assert load_skill("Commit Message")[0] == "commit-message"


def test_load_skill_resolves_a_unique_substring(skills_home):
    _write(skills_home, "commit-message.md", WELL_FORMED)
    _write(skills_home, "weekly-review.md", "checklist")
    assert load_skill("commit")[0] == "commit-message"


def test_load_skill_refuses_an_ambiguous_substring(skills_home):
    _write(skills_home, "review-code.md", "a")
    _write(skills_home, "review-docs.md", "b")
    with pytest.raises(SkillNotFound):
        load_skill("review")


def test_load_skill_unknown_name_raises(skills_home):
    _write(skills_home, "commit-message.md", WELL_FORMED)
    with pytest.raises(SkillNotFound):
        load_skill("deploy")


def test_load_skill_vanished_file_raises(skills_home, monkeypatch):
    _write(skills_home, "here.md", "content")
    (skill,) = list_skills()
    # Simulate the file disappearing between discovery and read; monkeypatch
    # restores the real list_skills itself on teardown.
    monkeypatch.setattr(
        "vegapunk.skills.list_skills",
        lambda: [Skill(name=skill.name, description=skill.description, path=skills_home / "gone.md")],
    )
    with pytest.raises(SkillNotFound):
        load_skill("here")


def test_bom_marked_file_still_parses_frontmatter(skills_home):
    # Windows Notepad and friends write a UTF-8 BOM; it must not break fence
    # detection or leak into the ad (read with utf-8-sig).
    (skills_home / "bommed.md").write_bytes(
        b"\xef\xbb\xbf---\ndescription: Survives a BOM\n---\nbody text"
    )
    (skill,) = list_skills()
    assert skill.description == "Survives a BOM"
    assert load_skill("bommed")[1] == "body text"


def test_skill_not_found_carries_the_available_names(skills_home):
    # Error paths list alternatives from the exception — no second discovery
    # pass, so malformed-file notes never print twice for one lookup.
    _write(skills_home, "commit-message.md", "body")
    with pytest.raises(SkillNotFound) as exc_info:
        load_skill("deploy")
    assert exc_info.value.available == ["commit-message"]


def test_system_block_empty_when_no_skills(skills_home):
    assert as_system_block() == ""


def test_system_block_advertises_each_skill_and_the_tool(skills_home):
    _write(skills_home, "commit-message.md", WELL_FORMED)
    _write(skills_home, "weekly-review.md", "# Weekly review\nsteps")
    block = as_system_block()
    assert "use_skill" in block
    assert "- commit-message — How to write a commit message for this repo" in block
    assert "- weekly-review — Weekly review" in block


def test_cli_main_seeds_session_with_skill_ads(skills_home, monkeypatch):
    # Pin the wiring: cli.main must fold the skills stanza into the system
    # prompt it builds. Same capture pattern as the memory seeding test.
    from vegapunk import cli
    from vegapunk.prompter import ScriptedPrompter

    _write(skills_home, "commit-message.md", WELL_FORMED)

    captured: dict[str, str] = {}

    class _CapturingSession:
        def __init__(self, brain, tools, system_prompt="", **kwargs):
            captured["system_prompt"] = system_prompt
            self.brain = brain  # main() reads session.brain for the banner

    monkeypatch.setattr("vegapunk.cli.Session", _CapturingSession)
    cli.main(prompter=ScriptedPrompter([EOFError]))

    assert "commit-message — How to write a commit message" in captured["system_prompt"]
    assert "use_skill" in captured["system_prompt"]
