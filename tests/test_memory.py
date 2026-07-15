"""Tests for Vegapunk's long-term memory — the DB-backed store + the remember tool.

Memory now lives in the embedded database; the autouse ``_isolated_vegapunk_home``
fixture (conftest) points ``db.db_path`` at a per-test tmp file, so these tests
never touch the developer's real ``.vegapunk/``. The embedding path is not wired
in yet (that build step adds it), so recall here is pure text match.
"""

from __future__ import annotations

import struct
from dataclasses import replace

import pytest

from vegapunk import db
from vegapunk.config import config
from vegapunk.embedding import EmbeddingError
from vegapunk.memory import (
    as_system_block,
    forget_memory,
    list_memory,
    load_memory,
    recall_memory,
    save_memory,
)
from vegapunk.tools import ALL_TOOLS
from vegapunk.tools.memory import remember


def _insert_fact_with_vector(fact_id: str, content: str, vector: list[float]) -> None:
    now = db.utcnow()
    db.execute(
        "INSERT INTO memory (id, kind, content, created_at, updated_at, embedding) "
        "VALUES (?, 'fact', ?, ?, ?, ?)",
        (fact_id, content, now, now, struct.pack(f"<{len(vector)}f", *vector)),
    )


def test_load_memory_empty_when_nothing_saved():
    assert load_memory() == ""


def test_save_memory_creates_dated_bullet():
    result = save_memory("prefers ruff over flake8")

    assert "prefers ruff over flake8" in result
    contents = load_memory()
    assert "prefers ruff over flake8" in contents
    assert contents.startswith("- [")  # dated bullet


def test_save_memory_accumulates_across_calls():
    save_memory("first fact")
    save_memory("second fact")

    contents = load_memory()
    assert "first fact" in contents
    assert "second fact" in contents
    assert contents.count("\n") == 2  # one bullet per fact


def test_load_memory_degrades_when_db_unavailable(monkeypatch, capsys):
    def _boom(*args, **kwargs):
        raise db.StoreError("db is toast")

    monkeypatch.setattr("vegapunk.db.query", _boom)

    assert load_memory() == ""  # degrades to "no memory" instead of raising
    assert "could not read the database" in capsys.readouterr().err


def test_save_memory_empty_is_noop():
    result = save_memory("   ")  # whitespace only

    assert "Nothing to remember" in result
    assert load_memory() == ""  # no empty row written


def test_as_system_block_empty_when_no_memory():
    assert as_system_block() == ""


def test_as_system_block_contains_memory_when_present():
    save_memory("deploys from main")

    block = as_system_block()
    assert "deploys from main" in block
    assert "remember about the user" in block  # labelled for the model


def test_list_memory_returns_typed_rows():
    save_memory("uses zsh")
    save_memory("works in the Pacific timezone")

    rows = list_memory()
    assert [r.content for r in rows] == ["uses zsh", "works in the Pacific timezone"]
    assert all(r.kind == "fact" for r in rows)
    assert all(len(r.id) == 32 for r in rows)  # uuid4 hex
    assert all(r.created_at.endswith("Z") for r in rows)


def test_forget_memory_by_unique_prefix():
    save_memory("a keeper")
    save_memory("a goner")
    goner = next(r for r in list_memory() if r.content == "a goner")

    result = forget_memory(goner.id[:8])
    assert "Forgot: a goner" == result
    assert [r.content for r in list_memory()] == ["a keeper"]


def test_forget_memory_ambiguous_prefix_refuses(monkeypatch):
    # Two rows whose ids share a prefix — force it by stubbing the lookup.
    def _two(sql, params=()):
        return [("id_aaa", "one"), ("id_aab", "two")]

    monkeypatch.setattr("vegapunk.db.query", _two)
    result = forget_memory("aa")
    assert "ambiguous" in result and "2 facts" in result


def test_forget_memory_unknown_prefix():
    assert "No memory fact matches" in forget_memory("deadbeef")


def test_forget_memory_rejects_non_hex():
    assert forget_memory("not-hex!").startswith("Usage:")


def test_recall_memory_matches_by_substring():
    save_memory("likes strong espresso")
    save_memory("dislikes decaf")
    save_memory("prefers the window seat")

    hits = recall_memory("espresso")
    assert [h.content for h in hits] == ["likes strong espresso"]

    assert recall_memory("nothing here") == []


def test_recall_memory_orders_by_cosine_distance(monkeypatch):
    from test_embedding import _fake_openai  # sibling test module (tests/ on sys.path)

    _insert_fact_with_vector("a", "alpha", [1.0, 0.0, 0.0, 0.0])
    _insert_fact_with_vector("b", "beta", [0.9, 0.1, 0.0, 0.0])
    _insert_fact_with_vector("c", "gamma", [0.0, 0.0, 1.0, 0.0])
    # The query embeds to [1,0,0,0]: nearest is alpha, then beta; gamma is orthogonal.
    monkeypatch.setattr("vegapunk.embedding.enabled", lambda: True)
    monkeypatch.setattr("vegapunk.embedding.config", replace(config, embed_model="m"))
    monkeypatch.setattr("vegapunk.embedding.OpenAI", _fake_openai({"find": [1.0, 0.0, 0.0, 0.0]}))

    hits = recall_memory("find", limit=2)
    assert [h.content for h in hits] == ["alpha", "beta"]


def test_recall_memory_like_fallback_when_disabled():
    # A fact WITH an embedding, but embeddings disabled (conftest) -> text-match path.
    _insert_fact_with_vector("x", "likes espresso", [1.0, 0.0, 0.0, 0.0])
    assert [h.content for h in recall_memory("espresso")] == ["likes espresso"]


def test_recall_memory_falls_back_to_text_when_vector_query_fails(monkeypatch, capsys):
    from test_embedding import _fake_openai  # sibling test module

    _insert_fact_with_vector("x", "likes espresso", [1.0, 0.0, 0.0, 0.0])
    monkeypatch.setattr("vegapunk.embedding.enabled", lambda: True)
    monkeypatch.setattr("vegapunk.embedding.config", replace(config, embed_model="m"))
    monkeypatch.setattr("vegapunk.embedding.OpenAI", _fake_openai({"espresso": [1.0, 0.0, 0.0, 0.0]}))

    # Make only the vector SELECT blow up; the LIKE fallback must still find the row.
    real_query = db.query

    def _query(sql, params=()):
        if "vector_distance_cos" in sql:
            raise db.StoreError("no vector support")
        return real_query(sql, params)

    monkeypatch.setattr("vegapunk.db.query", _query)

    hits = recall_memory("espresso")
    assert [h.content for h in hits] == ["likes espresso"]  # fell back, not empty
    assert "using text match" in capsys.readouterr().err


def test_save_memory_survives_embedding_failure(monkeypatch, capsys):
    monkeypatch.setattr("vegapunk.embedding.enabled", lambda: True)
    monkeypatch.setattr("vegapunk.embedding.config", replace(config, embed_model="m"))

    def _boom(_texts):
        raise EmbeddingError("backend down")

    monkeypatch.setattr("vegapunk.embedding.embed", _boom)

    result = save_memory("keep me anyway")
    assert "keep me anyway" in result  # the fact is saved
    stored = db.query("SELECT embedding FROM memory WHERE content = 'keep me anyway'")[0][0]
    assert stored is None  # embedding is NULL, not lost
    assert "embedding failed" in capsys.readouterr().err


def test_remember_tool_saves_and_confirms():
    result = remember("works in the Pacific timezone")

    assert "works in the Pacific timezone" in result
    assert "works in the Pacific timezone" in load_memory()


def test_recall_tool_registered_and_returns_hits():
    from vegapunk.tools.memory import recall

    tool = next(t for t in ALL_TOOLS if t.name == "recall")
    assert tool.guarded is False  # read-only lookup, no approval gate

    save_memory("likes espresso")
    assert "likes espresso" in recall("espresso")
    assert recall("nothing at all").startswith("No matching")


def test_remember_tool_registered_and_unguarded():
    tool = next(t for t in ALL_TOOLS if t.name == "remember")
    assert tool.guarded is False  # writes its own notebook, not the workspace
    schema = tool.to_schema()["function"]["parameters"]
    assert schema["properties"]["fact"] == {"type": "string"}
    assert schema["required"] == ["fact"]


def test_system_prompt_composition_includes_memory():
    # Exercises the exact expression cli.main uses to seed the session.
    from vegapunk.config import config

    save_memory("uses zsh")
    composed = config.system_prompt + as_system_block()

    assert "uses zsh" in composed


def test_cli_main_seeds_session_with_memory(monkeypatch):
    # Pin the wiring: cli.main must construct the Session with memory folded into
    # the system prompt. Capture the Session it builds (no model/TTY needed); an
    # immediate EOFError ends the REPL right after the session is created.
    from vegapunk import cli
    from vegapunk.prompter import ScriptedPrompter

    save_memory("prefers tabs over spaces")

    captured: dict[str, str] = {}

    class _CapturingSession:
        def __init__(self, brain, tools, system_prompt="", **kwargs):
            captured["system_prompt"] = system_prompt
            self.brain = brain  # main() reads session.brain for the banner

    monkeypatch.setattr("vegapunk.cli.Session", _CapturingSession)

    cli.main(prompter=ScriptedPrompter([EOFError]))

    assert "prefers tabs over spaces" in captured["system_prompt"]
