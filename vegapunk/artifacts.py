"""Immutable session text snapshots with bounded retrieval."""

from hashlib import sha256
from uuid import uuid4

from . import db
from .tools.workspace import resolve_in_workspace, workspace_root

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
    info = dict(zip(("id", "title", "size_bytes", "captured_bytes", "sha256", "complete", "characters"), rows[0]))
    files = db.query("SELECT path FROM artifact_files WHERE artifact_id=?", (artifact_id,))
    info["kind"] = "file_reference" if files else "text"
    if files:
        info["path"] = files[0][0]
        roots = db.query("SELECT root FROM artifact_workspaces WHERE artifact_id=?", (artifact_id,))
        info["workspace"] = roots[0][0] if roots else None
    return info


def read(session_id: str, artifact_id: str, cursor: int = 0) -> dict:
    """Read a bounded Unicode character page, including progress within long lines."""
    info = metadata(session_id, artifact_id)
    if info["kind"] == "file_reference":
        if info["workspace"] != str(workspace_root()):
            raise ValueError("File reference belongs to a different or unknown workspace; register it again explicitly")
        raw = _file_bytes(info["path"])
        text = raw.decode("utf-8")
        if isinstance(cursor, bool) or not 0 <= cursor <= len(text):
            raise ValueError("Invalid character cursor for current file")
        end = min(cursor + READ_CHARS, len(text))
        return {"artifact": info, "text": text[cursor:end],
                "changed": sha256(raw).hexdigest() != info["sha256"],
                "next_cursor": end if end < len(text) else None}
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
    info = metadata(session_id, artifact_id)
    if info["kind"] == "file_reference":
        raise ValueError("Search mutable files through workspace tools; read_artifact reports current file changes")
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


def _file_bytes(path: str) -> bytes:
    target = resolve_in_workspace(path)
    with target.open("rb") as handle:
        raw = handle.read(CAPTURE_BYTES + 1)
    if len(raw) > CAPTURE_BYTES:
        raise ValueError("File reference exceeds the 16 MiB read limit; use ranged workspace tools")
    return raw


def register_file(session_id: str, title: str, path: str) -> dict:
    """Register a mutable workspace text file without copying its content."""
    if not title.strip() or len(title) > 300:
        raise ValueError("Provide a title of 1 to 300 characters")
    target = resolve_in_workspace(path)
    relative = str(target.relative_to(workspace_root()))
    raw = _file_bytes(relative)
    text = raw.decode("utf-8")
    artifact_id = str(uuid4())
    with db.transaction(immediate=True) as conn:
        if not conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (session_id,)).fetchone():
            raise ValueError("Unknown session")
        conn.execute("INSERT INTO session_artifacts VALUES (?,?,?,?,?,?,?,?,?)",
                     (artifact_id, session_id, title.strip(), len(raw), 0, sha256(raw).hexdigest(), 0, len(text), db.utcnow()))
        conn.execute("INSERT INTO artifact_files VALUES (?,?)", (artifact_id, relative))
        conn.execute("INSERT INTO artifact_workspaces VALUES (?,?)", (artifact_id, str(workspace_root())))
    return metadata(session_id, artifact_id)


def link_checkpoint(session_id: str, artifact_id: str, checkpoint_id: str) -> None:
    """Associate existing evidence with a checkpoint atomically within its session."""
    with db.transaction(immediate=True) as conn:
        if not conn.execute("SELECT 1 FROM session_artifacts WHERE session_id=? AND artifact_id=?", (session_id, artifact_id)).fetchone():
            raise ValueError("Unknown artifact in this session")
        if not conn.execute("SELECT 1 FROM progress_checkpoints WHERE session_id=? AND checkpoint_id=?", (session_id, checkpoint_id)).fetchone():
            raise ValueError("Unknown checkpoint in this session")
        conn.execute("INSERT OR IGNORE INTO checkpoint_artifacts VALUES (?,?)", (checkpoint_id, artifact_id))


def checkpoint_artifacts(session_id: str, checkpoint_id: str) -> list[dict]:
    """Return linked artifact metadata, restricted to its owning session."""
    rows = db.query("SELECT a.artifact_id FROM session_artifacts a JOIN checkpoint_artifacts c ON c.artifact_id=a.artifact_id WHERE a.session_id=? AND c.checkpoint_id=? ORDER BY a.created_at LIMIT 100", (session_id, checkpoint_id))
    return [metadata(session_id, row[0]) for row in rows]
