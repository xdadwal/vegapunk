"""Immutable session text snapshots with bounded retrieval."""

from hashlib import sha256
from uuid import uuid4

from . import db

CAPTURE_BYTES = 16 * 1024 * 1024
CHUNK_CHARS = 16384
READ_CHARS = 12000


def store_text(session_id: str, title: str, text: str) -> dict:
    """Capture text up to the storage limit, labelling incomplete captures."""
    if not title.strip() or len(title) > 300:
        raise ValueError("Provide a title of 1 to 300 characters")
    original = text.encode("utf-8")
    captured = original[:CAPTURE_BYTES].decode("utf-8", errors="ignore")
    raw = captured.encode("utf-8")
    artifact_id = str(uuid4())
    with db.transaction(immediate=True) as conn:
        if not conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (session_id,)).fetchone():
            raise ValueError("Unknown session")
        conn.execute("INSERT INTO session_artifacts VALUES (?,?,?,?,?,?,?,?,?)",
                     (artifact_id, session_id, title.strip(), len(original), len(raw), sha256(raw).hexdigest(),
                      int(len(original) == len(raw)), len(captured), db.utcnow()))
        for position in range(0, len(captured), CHUNK_CHARS):
            conn.execute("INSERT INTO artifact_chunks VALUES (?,?,?)", (artifact_id, position // CHUNK_CHARS, captured[position:position+CHUNK_CHARS]))
    return metadata(session_id, artifact_id)


def metadata(session_id: str, artifact_id: str) -> dict:
    """Resolve an artifact only in its owning session."""
    rows = db.query("SELECT artifact_id,title,size_bytes,captured_bytes,digest,complete,characters FROM session_artifacts WHERE session_id=? AND artifact_id=?", (session_id, artifact_id))
    if not rows:
        raise ValueError("Unknown artifact in this session")
    return dict(zip(("id", "title", "size_bytes", "captured_bytes", "sha256", "complete", "characters"), rows[0]))


def read(session_id: str, artifact_id: str, cursor: int = 0) -> dict:
    """Read a bounded Unicode character page, including progress within long lines."""
    info = metadata(session_id, artifact_id)
    if isinstance(cursor, bool) or cursor < 0 or cursor > info["characters"]:
        raise ValueError("Invalid character cursor")
    end = min(cursor + READ_CHARS, info["characters"])
    rows = db.query("SELECT content FROM artifact_chunks WHERE artifact_id=? AND position>=? AND position<=? ORDER BY position",
                    (artifact_id, cursor // CHUNK_CHARS, end // CHUNK_CHARS))
    text = "".join(row[0] for row in rows)[cursor % CHUNK_CHARS:cursor % CHUNK_CHARS + end - cursor]
    return {"artifact": info, "text": text, "next_cursor": end if end < info["characters"] else None}


def list_artifacts(session_id: str, offset: int = 0) -> dict:
    """List up to twenty artifacts with metadata and a next-page offset."""
    if offset < 0:
        raise ValueError("Offset must be nonnegative")
    rows = db.query("SELECT artifact_id FROM session_artifacts WHERE session_id=? ORDER BY created_at,artifact_id LIMIT 21 OFFSET ?", (session_id, offset))
    return {"items": [metadata(session_id, row[0]) for row in rows[:20]], "next_offset": offset + 20 if len(rows) > 20 else None}


def search(session_id: str, artifact_id: str, query: str) -> list[dict]:
    """Find up to twenty literal, case-sensitive matches with line numbers."""
    metadata(session_id, artifact_id)
    if not query or len(query) > 1000:
        raise ValueError("Provide a search query of 1 to 1000 characters")
    # The stored snapshot is bounded by CAPTURE_BYTES; search never reads host files.
    rows = db.query("SELECT content FROM artifact_chunks WHERE artifact_id=? ORDER BY position", (artifact_id,))
    text = "".join(row[0] for row in rows)
    results, cursor = [], 0
    while len(results) < 20:
        found = text.find(query, cursor)
        if found < 0:
            break
        results.append({"line": text.count("\n", 0, found) + 1, "cursor": found,
                        "excerpt": text[max(0, found-100):found+200]})
        cursor = found + len(query)
    return results
