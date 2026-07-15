"""Tests for the embedding pipeline — packing, save-time embed, and startup sync.

Offline and deterministic: the OpenAI client is replaced with a fake that returns
hand-crafted vectors, and the autouse conftest fixture pins ``embedding.enabled``
off so nothing accidentally reaches the network.
"""

from __future__ import annotations

import struct
from dataclasses import replace
from types import SimpleNamespace

import pytest
from openai import OpenAIError

from vegapunk import db, embedding
from vegapunk.config import config


def _fake_openai(table: dict[str, list[float]], *, fail: bool = False):
    """A drop-in for embedding.OpenAI: create() looks each input up in ``table``."""

    class _Embeddings:
        def create(self, model, input):
            if fail:
                raise OpenAIError("backend down")
            data = [SimpleNamespace(index=i, embedding=table[t]) for i, t in enumerate(input)]
            return SimpleNamespace(data=data)

    class _Client:
        embeddings = _Embeddings()

    return lambda base_url, api_key: _Client()


@pytest.fixture
def embed_on(monkeypatch):
    """Enable embeddings with a stub model name (overrides the conftest pin)."""
    monkeypatch.setattr("vegapunk.embedding.enabled", lambda: True)
    monkeypatch.setattr("vegapunk.embedding.config", replace(config, embed_model="test-model"))
    return monkeypatch


def _insert_fact(fact_id: str, content: str, embedding_blob: bytes | None = None) -> None:
    now = db.utcnow()
    db.execute(
        "INSERT INTO memory (id, kind, content, created_at, updated_at, embedding) "
        "VALUES (?, 'fact', ?, ?, ?, ?)",
        (fact_id, content, now, now, embedding_blob),
    )


def test_embed_one_or_none_returns_none_when_disabled():
    # conftest pins enabled() -> False
    assert embedding.embed_one_or_none("anything") is None


def test_embed_packs_little_endian_f32(embed_on):
    embed_on.setattr("vegapunk.embedding.OpenAI", _fake_openai({"hi": [1.0, 2.0, 3.0, 4.0]}))
    assert embedding.embed(["hi"]) == [struct.pack("<4f", 1.0, 2.0, 3.0, 4.0)]


def test_embed_raises_on_backend_error(embed_on):
    embed_on.setattr("vegapunk.embedding.OpenAI", _fake_openai({}, fail=True))
    with pytest.raises(embedding.EmbeddingError):
        embedding.embed(["hi"])


def test_embed_wraps_malformed_response(embed_on):
    # A beta/local server returning a non-conformant body (here: data=None) must
    # surface as EmbeddingError, not a raw TypeError that would lose a fact.
    class _Bad:
        embeddings = SimpleNamespace(create=lambda **kw: SimpleNamespace(data=None))

    embed_on.setattr("vegapunk.embedding.OpenAI", lambda base_url, api_key: _Bad())
    with pytest.raises(embedding.EmbeddingError):
        embedding.embed(["hi"])


def test_sync_backfills_null_embeddings_only(embed_on):
    db.execute("INSERT INTO meta (key, value) VALUES ('embed_model', 'test-model')")  # no model change
    _insert_fact("id_a", "a")  # NULL -> should be filled
    already = struct.pack("<4f", 9.0, 9.0, 9.0, 9.0)
    _insert_fact("id_b", "b", already)  # already embedded -> untouched
    embed_on.setattr("vegapunk.embedding.OpenAI", _fake_openai({"a": [1.0, 0.0, 0.0, 0.0]}))

    embedding.sync_embeddings()

    rows = {r[0]: r[1] for r in db.query("SELECT id, embedding FROM memory")}
    assert rows["id_a"] == struct.pack("<4f", 1.0, 0.0, 0.0, 0.0)
    assert rows["id_b"] == already


def test_sync_wipes_and_reembeds_on_model_change(embed_on):
    db.execute("INSERT INTO meta (key, value) VALUES ('embed_model', 'old-model')")
    _insert_fact("id_a", "a", struct.pack("<4f", 9.0, 9.0, 9.0, 9.0))  # stale vector
    embed_on.setattr("vegapunk.embedding.OpenAI", _fake_openai({"a": [1.0, 0.0, 0.0, 0.0]}))

    embedding.sync_embeddings()

    assert db.query("SELECT embedding FROM memory WHERE id = 'id_a'")[0][0] == struct.pack(
        "<4f", 1.0, 0.0, 0.0, 0.0
    )
    assert db.query("SELECT value FROM meta WHERE key = 'embed_model'")[0][0] == "test-model"


def test_sync_survives_embedding_error(embed_on, capsys):
    db.execute("INSERT INTO meta (key, value) VALUES ('embed_model', 'test-model')")
    _insert_fact("id_a", "a")  # NULL
    embed_on.setattr("vegapunk.embedding.OpenAI", _fake_openai({}, fail=True))

    embedding.sync_embeddings()  # must not raise

    assert db.query("SELECT embedding FROM memory WHERE id = 'id_a'")[0][0] is None  # still NULL
    assert "embedding" in capsys.readouterr().err


def test_sync_is_noop_when_disabled():
    # conftest pins enabled() -> False; a NULL embedding stays NULL.
    _insert_fact("id_a", "a")
    embedding.sync_embeddings()
    assert db.query("SELECT embedding FROM memory WHERE id = 'id_a'")[0][0] is None
