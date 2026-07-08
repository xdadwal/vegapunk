"""Tests for the skills store — deterministic, no model/network.

These pin the Agent Skills consumption contract (https://agentskills.io) —
directory discovery, the frontmatter rules (top-level keys only, the directory
name wins), the loud-degrade cases — and the forgiving-but-safe name
resolution. Discovery is driven against a tmp_path via the same
directory-function monkeypatch seam the session-store tests use.
"""

from __future__ import annotations

import pytest

from vegapunk import skills
from vegapunk.skills import (
    Skill,
    SkillNotFound,
    as_system_block,
    file_reference_note,
    list_skills,
    load_skill,
)


@pytest.fixture
def skills_home(tmp_path, monkeypatch):
    monkeypatch.setattr("vegapunk.skills.skills_dir", lambda: tmp_path)
    return tmp_path


def _write_skill(home, name, text):
    """A skill in the Agent Skills layout: <name>/SKILL.md."""
    (home / name).mkdir(parents=True, exist_ok=True)
    (home / name / "SKILL.md").write_text(text, encoding="utf-8")


WELL_FORMED = """---
name: commit-message
description: How to write a commit message for this repo
---
# Commit messages

- type(scope): summary
"""


def test_well_formed_skill_parses_name_description_and_body(skills_home):
    _write_skill(skills_home, "commit-message", WELL_FORMED)
    (skill,) = list_skills()
    assert skill.name == "commit-message"
    assert skill.description == "How to write a commit message for this repo"
    assert skill.root == skills_home / "commit-message"

    found, body = load_skill("commit-message")
    assert found.name == "commit-message"
    assert "- type(scope): summary" in body
    assert "---" not in body  # the fence block is not part of the body


def test_name_is_the_directory_and_a_mismatch_earns_a_note(skills_home, capsys):
    # Content can't spoof identity: the directory names the skill; a
    # disagreeing frontmatter name is called out and overruled.
    _write_skill(skills_home, "my-fancy-skill", WELL_FORMED)  # says commit-message
    (skill,) = list_skills()
    assert skill.name == "my-fancy-skill"
    err = capsys.readouterr().err
    assert "says name 'commit-message'" in err
    assert "using the directory name" in err


def test_matching_frontmatter_name_earns_no_note(skills_home, capsys):
    _write_skill(skills_home, "commit-message", WELL_FORMED)
    list_skills()
    assert capsys.readouterr().err == ""


def test_quoted_description_is_unquoted(skills_home):
    _write_skill(skills_home, "a", '---\ndescription: "Quoted, like YAML"\n---\nbody')
    assert list_skills()[0].description == "Quoted, like YAML"


def test_unknown_frontmatter_keys_are_ignored(skills_home):
    _write_skill(
        skills_home,
        "a",
        "---\nlicense: Apache-2.0\ndescription: Real one\ncompatibility: Requires git\n"
        "allowed-tools: Bash(git:*) Read\n---\nbody",
    )
    assert list_skills()[0].description == "Real one"


def test_nested_metadata_blocks_are_not_top_level_keys(skills_home):
    # The spec allows a nested metadata map (and marketplaces use it); its
    # indented members — even one spelled "description" — must not override
    # the real top-level fields.
    _write_skill(
        skills_home,
        "lavish",
        "---\nname: lavish\ndescription: The real ad\nauthor: someone\n"
        "metadata:\n  hermes:\n    tags: [html, review]\n    description: sneaky\n"
        "---\nbody",
    )
    (skill,) = list_skills()
    assert skill.description == "The real ad"


def test_no_frontmatter_falls_back_to_first_line_as_description(skills_home):
    _write_skill(skills_home, "notes", "# Weekly review checklist\n\nDo the things.")
    (skill,) = list_skills()
    assert skill.description == "Weekly review checklist"  # heading markers stripped
    _, body = load_skill("notes")
    assert body.startswith("# Weekly review checklist")  # body keeps the whole file


def test_frontmatter_without_description_falls_back(skills_home):
    _write_skill(skills_home, "a", "---\nauthor: bob\n---\nFirst real line\nmore")
    assert list_skills()[0].description == "First real line"


def test_long_description_is_collapsed_and_capped(skills_home):
    long = "words " * 40  # ~240 chars with internal runs
    _write_skill(skills_home, "a", f"---\ndescription: {long}\n---\nbody")
    description = list_skills()[0].description
    assert len(description) == 100
    assert description.endswith("…")
    assert "\n" not in description


def test_unclosed_frontmatter_degrades_to_whole_file_with_a_note(skills_home, capsys):
    _write_skill(skills_home, "broken", "---\ndescription: never closed\nThe actual content")
    (skill,) = list_skills()
    _, body = load_skill("broken")
    assert "The actual content" in body
    assert body.startswith("---")  # whole file treated as body — nothing hidden
    assert "broken/SKILL.md: unclosed frontmatter" in capsys.readouterr().err


def test_empty_skill_is_skipped_with_a_note(skills_home, capsys):
    _write_skill(skills_home, "empty", "---\ndescription: all hat no cattle\n---\n   \n")
    _write_skill(skills_home, "real", "content")
    assert [s.name for s in list_skills()] == ["real"]
    assert "empty skill body" in capsys.readouterr().err


def test_unreadable_manifest_is_skipped_and_siblings_survive(skills_home, capsys):
    (skills_home / "binary").mkdir()
    (skills_home / "binary" / "SKILL.md").write_bytes(b"\xff\xfe\x00 not utf-8 \xff")
    _write_skill(skills_home, "good", "fine content")
    assert [s.name for s in list_skills()] == ["good"]
    assert "could not read binary/SKILL.md" in capsys.readouterr().err


def test_spec_invalid_directory_names_are_skipped_with_a_note(skills_home, capsys):
    for bad in ("My Skill", "double--hyphen", "-edge"):
        _write_skill(skills_home, bad, "content")
    _write_skill(skills_home, "fine-name", "content")
    assert [s.name for s in list_skills()] == ["fine-name"]
    err = capsys.readouterr().err
    assert err.count("not a valid skill name") == 3


def test_directory_without_a_manifest_is_skipped_with_a_note(skills_home, capsys):
    (skills_home / "no-manifest").mkdir()
    (skills_home / "no-manifest" / "notes.txt").write_text("not a skill", encoding="utf-8")
    assert list_skills() == []
    assert "no-manifest/: no SKILL.md" in capsys.readouterr().err


def test_legacy_flat_skill_file_gets_a_migration_nudge(skills_home, capsys):
    (skills_home / "commit-message.md").write_text("old-style skill", encoding="utf-8")
    assert list_skills() == []
    err = capsys.readouterr().err
    assert "flat skill files are no longer read" in err
    assert "commit-message/SKILL.md" in err  # tells the user exactly where to move it


def test_stray_files_and_hidden_dirs_are_ignored_silently(skills_home, capsys):
    (skills_home / "notes.txt").write_text("not a skill", encoding="utf-8")
    (skills_home / "skill.md~").write_text("editor backup", encoding="utf-8")
    (skills_home / ".git").mkdir()
    assert list_skills() == []
    assert capsys.readouterr().err == ""  # not an error, not even a note


def test_missing_dir_lists_nothing_and_does_not_create_it(tmp_path, monkeypatch):
    ghost = tmp_path / "never-made"
    monkeypatch.setattr("vegapunk.skills.skills_dir", lambda: ghost)
    assert list_skills() == []
    assert not ghost.exists()


def test_load_skill_is_forgiving_about_case_and_spacing(skills_home):
    _write_skill(skills_home, "commit-message", WELL_FORMED)
    assert load_skill("Commit Message")[0].name == "commit-message"


def test_load_skill_resolves_a_unique_substring(skills_home):
    _write_skill(skills_home, "commit-message", WELL_FORMED)
    _write_skill(skills_home, "weekly-review", "checklist")
    assert load_skill("commit")[0].name == "commit-message"


def test_load_skill_refuses_an_ambiguous_substring(skills_home):
    _write_skill(skills_home, "review-code", "a")
    _write_skill(skills_home, "review-docs", "b")
    with pytest.raises(SkillNotFound):
        load_skill("review")


def test_load_skill_unknown_name_raises(skills_home):
    _write_skill(skills_home, "commit-message", WELL_FORMED)
    with pytest.raises(SkillNotFound):
        load_skill("deploy")


def test_load_skill_vanished_manifest_raises(skills_home, monkeypatch):
    _write_skill(skills_home, "here", "content")
    (skill,) = list_skills()
    # Simulate the manifest disappearing between discovery and read; monkeypatch
    # restores the real list_skills itself on teardown.
    monkeypatch.setattr(
        "vegapunk.skills.list_skills",
        lambda: [
            Skill(
                name=skill.name,
                description=skill.description,
                path=skills_home / "gone" / "SKILL.md",
            )
        ],
    )
    with pytest.raises(SkillNotFound):
        load_skill("here")


def test_bom_marked_manifest_still_parses_frontmatter(skills_home):
    # Windows Notepad and friends write a UTF-8 BOM; it must not break fence
    # detection or leak into the ad (read with utf-8-sig).
    (skills_home / "bommed").mkdir()
    (skills_home / "bommed" / "SKILL.md").write_bytes(
        b"\xef\xbb\xbf---\ndescription: Survives a BOM\n---\nbody text"
    )
    (skill,) = list_skills()
    assert skill.description == "Survives a BOM"
    assert load_skill("bommed")[1] == "body text"


def test_skill_not_found_carries_the_available_names(skills_home):
    # Error paths list alternatives from the exception — no second discovery
    # pass, so malformed-file notes never print twice for one lookup.
    _write_skill(skills_home, "commit-message", "body")
    with pytest.raises(SkillNotFound) as exc_info:
        load_skill("deploy")
    assert exc_info.value.available == ["commit-message"]


def test_file_reference_note_only_when_the_skill_bundles_files(skills_home):
    _write_skill(skills_home, "bare", "just instructions")
    _write_skill(skills_home, "bundled", "run scripts/go.py")
    (skills_home / "bundled" / "scripts").mkdir()
    (skills_home / "bundled" / "scripts" / "go.py").write_text("print('hi')", encoding="utf-8")

    bare, bundled = list_skills()
    assert file_reference_note(bare) == ""
    note = file_reference_note(bundled)
    assert str(bundled.root) in note  # tells the model where relative paths resolve


def test_file_reference_note_ignores_hidden_files(skills_home):
    # A stray .DS_Store isn't a bundled file — no false pointer.
    _write_skill(skills_home, "tidy", "instructions")
    (skills_home / "tidy" / ".DS_Store").write_bytes(b"\x00")
    (skill,) = list_skills()
    assert file_reference_note(skill) == ""


def test_spec_length_names_are_matchable(skills_home):
    # The spec allows names up to 64 chars; the advertised name must be
    # exactly what load_skill can resolve (slugify's default cap is shorter).
    long_name = "a" * 50
    _write_skill(skills_home, long_name, "content")
    assert load_skill(long_name)[0].name == long_name


def test_frontmatter_line_without_a_colon_never_raises(skills_home):
    # list_skills promises never to raise; a bare word in the fence must not
    # break parsing (partition degrades, split-unpacking would not).
    _write_skill(skills_home, "odd", "---\njustaword\ndescription: Still parsed\n---\nbody")
    (skill,) = list_skills()
    assert skill.description == "Still parsed"


def test_dash_separator_inside_the_body_is_preserved(skills_home):
    # Only the FIRST closing fence ends the frontmatter; a later --- is body.
    _write_skill(skills_home, "ruled", "---\ndescription: d\n---\nabove\n---\nbelow")
    _, body = load_skill("ruled")
    assert body == "above\n---\nbelow"


def test_empty_frontmatter_fence_degrades_to_body_fallback(skills_home):
    _write_skill(skills_home, "hollow", "---\n---\nJust the body")
    (skill,) = list_skills()
    assert skill.description == "Just the body"


def test_tab_indented_nested_keys_are_not_top_level(skills_home):
    _write_skill(
        skills_home,
        "tabbed",
        "---\ndescription: Real\nmetadata:\n\tdescription: sneaky-tab\n---\nbody",
    )
    assert list_skills()[0].description == "Real"


def test_system_block_empty_when_no_skills(skills_home):
    assert as_system_block() == ""


def test_system_block_advertises_each_skill_and_the_tool(skills_home):
    _write_skill(skills_home, "commit-message", WELL_FORMED)
    _write_skill(skills_home, "weekly-review", "# Weekly review\nsteps")
    block = as_system_block()
    assert "use_skill" in block
    assert "- commit-message — How to write a commit message for this repo" in block
    assert "- weekly-review — Weekly review" in block


def test_cli_main_seeds_session_with_skill_ads(skills_home, monkeypatch):
    # Pin the wiring: cli.main must fold the skills stanza into the system
    # prompt it builds. Same capture pattern as the memory seeding test.
    from vegapunk import cli
    from vegapunk.prompter import ScriptedPrompter

    _write_skill(skills_home, "commit-message", WELL_FORMED)

    captured: dict[str, str] = {}

    class _CapturingSession:
        def __init__(self, brain, tools, system_prompt="", **kwargs):
            captured["system_prompt"] = system_prompt
            self.brain = brain  # main() reads session.brain for the banner

    monkeypatch.setattr("vegapunk.cli.Session", _CapturingSession)
    cli.main(prompter=ScriptedPrompter([EOFError]))

    assert "commit-message — How to write a commit message" in captured["system_prompt"]
    assert "use_skill" in captured["system_prompt"]
