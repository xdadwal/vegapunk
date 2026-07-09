"""Embeddings for semantic memory recall.

Vectors are produced by Docker Model Runner's OpenAI-compatible ``/embeddings``
endpoint (the same server the local brain talks to) and stored as little-endian
float32 blobs in ``memory.embedding``. Everything here is opt-in: with no
``VEGAPUNK_EMBED_MODEL`` configured, ``enabled()`` is false, memory still works,
and recall falls back to text matching.

Callers on the save/startup paths must gate on the module-level ``enabled()`` (not
on ``config.embed_model`` directly) — that function is the suite's no-network seam,
monkeypatched off by default in tests.
"""

from __future__ import annotations

import struct
import sys

from openai import OpenAI, OpenAIError

from . import db
from .config import config


class EmbeddingError(Exception):
    """The embedding backend failed or is unreachable."""


def enabled() -> bool:
    """True when an embedding model is configured."""
    return bool(config.embed_model)


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def embed(texts: list[str]) -> list[bytes]:
    """Embed each text, returning packed float32 blobs in the same order.

    Raises ``EmbeddingError`` if disabled (a programming error) or if the backend
    request fails.
    """
    if not enabled():
        raise EmbeddingError("no embedding model configured")
    # Guard the whole region: a beta/local server can return a malformed body,
    # and the response parse/pack must fail as an EmbeddingError (best-effort for
    # callers) rather than a raw crash that loses a fact or breaks startup.
    try:
        client = OpenAI(base_url=config.base_url, api_key=config.api_key)
        response = client.embeddings.create(model=config.embed_model, input=texts)
        ordered = sorted(response.data, key=lambda item: item.index)
        return [_pack(item.embedding) for item in ordered]
    except (OpenAIError, AttributeError, TypeError, ValueError, struct.error) as exc:
        raise EmbeddingError(f"embedding request failed: {exc}") from exc


def embed_one_or_none(text: str) -> bytes | None:
    """Best-effort single embed for save paths: ``None`` when disabled, and
    ``None`` (with a stderr note) on failure — a fact is never lost to an
    embedding problem."""
    if not enabled():
        return None
    try:
        return embed([text])[0]
    except EmbeddingError as exc:
        print(f"  [memory] embedding failed (fact saved without): {exc}", file=sys.stderr)
        return None


def sync_embeddings() -> None:
    """Reconcile stored embeddings with the configured model at startup.

    A no-op when disabled. If the model changed (or was never recorded), all
    embeddings are dropped and recomputed — they are derived data. Then any rows
    still missing an embedding are backfilled in batches. Entirely best-effort:
    every failure degrades to a stderr note and never raises.
    """
    if not enabled():
        return
    model = config.embed_model
    try:
        row = db.query("SELECT value FROM meta WHERE key = 'embed_model'")
        stored = row[0][0] if row else None
        if stored != model:
            db.execute("UPDATE memory SET embedding = NULL")
            db.execute(
                "INSERT INTO meta (key, value) VALUES ('embed_model', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (model,),
            )
        pending = db.query(
            "SELECT id, content FROM memory WHERE embedding IS NULL ORDER BY created_at"
        )
    except db.StoreError as exc:
        print(f"  [memory] embedding sync skipped: {exc}", file=sys.stderr)
        return
    if not pending:
        return

    done = 0
    batch = 32
    try:
        for start in range(0, len(pending), batch):
            chunk = pending[start : start + batch]
            vectors = embed([content for _, content in chunk])
            with db.transaction() as conn:
                for (fact_id, _), vector in zip(chunk, vectors):
                    conn.execute("UPDATE memory SET embedding = ? WHERE id = ?", (vector, fact_id))
            done += len(chunk)
    except EmbeddingError as exc:
        print(f"  [memory] embedding backfill stopped after {done}: {exc}", file=sys.stderr)
    except db.StoreError as exc:
        print(f"  [memory] embedding backfill failed after {done}: {exc}", file=sys.stderr)

    if done:
        print(f"  [memory] embedded {done} facts with {model}", file=sys.stderr)
