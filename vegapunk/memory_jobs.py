"""Durable, incremental conversation processing and human memory decisions."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass

from . import db, memory
from .config import config
from .memory_extraction import Candidate, ExtractionError, ModelExtractor, Source, sources_from_messages

_QUIET_SECONDS = 60
_MAX_ATTEMPTS = 3
_BATCH_CHARS = 12000


@dataclass(frozen=True)
class SavedCandidate:
    id: str
    content: str
    status: str
    topic: str
    confidence: float
    session_slug: str | None
    source_id: str
    quote: str


def _prefix(sources: list[Source]) -> str:
    encoded = json.dumps([asdict(s) for s in sources], sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def paused() -> bool:
    return db.query("SELECT value FROM meta WHERE key='memory_paused'") == [("1",)]


def set_paused(value: bool) -> None:
    db.execute("INSERT INTO meta(key,value) VALUES ('memory_paused',?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("1" if value else "0",))


def discover_jobs() -> None:
    """Backfill jobs from saved sessions without putting model work on autosave."""
    with db.transaction() as conn:
        conn.execute("DELETE FROM memory_jobs WHERE session_slug NOT IN (SELECT slug FROM sessions)")
        conn.execute("INSERT OR IGNORE INTO memory_jobs(session_slug) SELECT slug FROM sessions")


def _activate(conn, content: str, candidate_id: str, stamp: str) -> str | None:
    # Explicitly remembered facts stay owned by the user. A duplicate extracted
    # candidate must not gain the ability to delete that pre-existing memory.
    fingerprint = memory.fingerprint(content)
    existing = conn.execute("SELECT content FROM memory WHERE kind='fact'").fetchall()
    if any(memory.fingerprint(row[0]) == fingerprint for row in existing):
        return None
    memory_id = db.new_id()
    conn.execute("INSERT INTO memory(id,kind,content,created_at,updated_at,metadata) "
                 "VALUES (?,'fact',?,?,?,?)", (memory_id, content, stamp, stamp,
                                               json.dumps({"candidate_id": candidate_id})))
    return memory_id


def _store_candidate(conn, item: Candidate, slug: str, stamp: str) -> None:
    fingerprint = memory.fingerprint(item.content)
    if conn.execute("SELECT id FROM memory_candidates WHERE fingerprint=?", (fingerprint,)).fetchone():
        return  # includes rejected/forgotten candidates: do not relearn them
    conflict = conn.execute("SELECT id FROM memory_candidates WHERE topic=? AND status='active'",
                            (item.topic,)).fetchone()
    status = "active" if (config.memory_review == "auto" and item.explicit
                           and item.confidence >= 0.9 and not conflict) else "pending"
    candidate_id = db.new_id()
    memory_id = _activate(conn, item.content, candidate_id, stamp) if status == "active" else None
    conn.execute("INSERT INTO memory_candidates "
                 "(id,fingerprint,content,topic,category,confidence,status,session_slug,"
                 "source_id,quote,memory_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (candidate_id, fingerprint, item.content, item.topic, item.category,
                  item.confidence, status, slug, item.source_id, item.quote, memory_id, stamp))


def process_one(
    extract: Callable[[list[Source]], list[Candidate]], *, now: str | None = None,
    scan_before: str | None = None,
) -> bool:
    """Process one bounded batch; persist failures and never block interactive chat."""
    if not config.memory_enabled or paused():
        return False
    discover_jobs()
    stamp = now or db.utcnow()
    cutoff = db.stamp_plus(stamp, -_QUIET_SECONDS)
    if scan_before is not None:
        cutoff = min(cutoff, scan_before)
    token = db.new_id()
    with db.transaction(immediate=True) as conn:
        row = conn.execute(
            "SELECT j.session_slug,j.cursor,j.prefix_hash,j.attempts,j.source_revision,s.updated_at "
            "FROM memory_jobs j JOIN sessions s ON s.slug=j.session_slug "
            "WHERE s.updated_at<=? AND ((j.status='pending' AND j.next_run_at<=?) "
            "OR (j.status='running' AND j.lease_until<=?) "
            "OR (j.status IN ('complete','error') AND j.source_revision<>s.updated_at)) "
            "ORDER BY j.updated_at,s.updated_at LIMIT 1", (cutoff, stamp, stamp)).fetchone()
        if not row:
            return False
        slug, cursor, prefix_hash, attempts, old_revision, revision = row
        conn.execute("UPDATE memory_jobs SET status='running',lease_token=?,lease_until=? "
                     "WHERE session_slug=?", (token, db.stamp_plus(stamp, config.memory_timeout + 60), slug))
    try:
        source_rows = db.query("SELECT messages FROM sessions WHERE slug=?", (slug,))
        if not source_rows:
            return True
        messages = json.loads(source_rows[0][0])
        if not isinstance(messages, list):
            raise ValueError("Saved conversation is not a message list")
        sources = sources_from_messages(messages)
        if cursor > len(sources) or (cursor and _prefix(sources[:cursor]) != prefix_hash):
            cursor = 0
        end, size = cursor, 0
        while end < len(sources) and size + len(sources[end].text) <= _BATCH_CHARS:
            size += len(sources[end].text)
            end += 1
        batch = sources[cursor:end]
        items = extract(batch) if batch else []
        completed_prefix = _prefix(sources[:end])
        with db.transaction(immediate=True) as conn:
            current = conn.execute("SELECT s.messages,s.updated_at FROM sessions s "
                                   "JOIN memory_jobs j ON j.session_slug=s.slug "
                                   "WHERE s.slug=? AND j.lease_token=?", (slug, token)).fetchone()
            if not current:
                return True  # deleted or renamed while the model was running
            current_sources = sources_from_messages(json.loads(current[0]))
            is_paused = conn.execute("SELECT value FROM meta WHERE key='memory_paused'").fetchone()
            stale = len(current_sources) < end or _prefix(current_sources[:end]) != completed_prefix
            if stale or (is_paused and is_paused[0] == "1"):
                conn.execute("UPDATE memory_jobs SET status='pending',lease_token=NULL,lease_until=NULL "
                             "WHERE session_slug=? AND lease_token=?", (slug, token))
                return True
            for item in items:
                _store_candidate(conn, item, slug, stamp)
            status = "pending" if end < len(current_sources) else "complete"
            conn.execute("UPDATE memory_jobs SET cursor=?,prefix_hash=?,source_revision=?,status=?,"
                         "attempts=0,next_run_at=?,lease_token=NULL,lease_until=NULL,last_error=NULL,updated_at=? "
                         "WHERE session_slug=? AND lease_token=?",
                         (end, completed_prefix, current[1], status, stamp, stamp, slug, token))
    except Exception as exc:  # unattended boundary; a failed batch must remain retryable
        # Backoff starts after the failed call, which may itself take minutes.
        # Keep an injected eligibility clock from moving backward in tests.
        failed_at = max(stamp, db.utcnow())
        attempts = (0 if old_revision != revision else attempts) + 1
        status = "error" if attempts >= _MAX_ATTEMPTS else "pending"
        # Provider exception text may echo private input; only persist its type.
        error = str(exc) if isinstance(exc, ExtractionError) else f"{type(exc).__name__}: extraction failed"
        db.execute("UPDATE memory_jobs SET status=?,attempts=?,next_run_at=?,last_error=?,source_revision=?,"
                   "lease_token=NULL,lease_until=NULL,updated_at=? WHERE session_slug=? AND lease_token=?",
                   (status, attempts, db.stamp_plus(failed_at, 60 * 2 ** (attempts - 1)), error[:200],
                    revision, failed_at, slug, token))
        print(f"  [memory] extraction failed ({type(exc).__name__}); see /memory jobs", file=sys.stderr)
    return True


def candidates(status: str = "pending") -> list[SavedCandidate]:
    rows = db.query("SELECT id,content,status,topic,confidence,session_slug,source_id,quote "
                    "FROM memory_candidates WHERE status=? ORDER BY created_at,id", (status,))
    return [SavedCandidate(*row) for row in rows]


def decide(prefix: str, action: str) -> str:
    """Approve or reject one extracted candidate, preserving a suppression record."""
    if action not in {"approve", "reject"} or not re.fullmatch(r"[a-fA-F0-9]+", prefix):
        return "Usage: /memory approve|reject <candidate-id>"
    with db.transaction(immediate=True) as conn:
        rows = conn.execute("SELECT id,content,status,memory_id,session_slug,source_id,quote,topic "
                            "FROM memory_candidates "
                            "WHERE id LIKE ? || '%'", (prefix.lower(),)).fetchall()
        if len(rows) != 1:
            return "No matching candidate." if not rows else "Ambiguous candidate id; use more characters."
        candidate_id, content, status, memory_id, slug, source_id, quote, topic = rows[0]
        if action == "approve":
            if status == "active":
                return "Memory is already active."
            source = conn.execute("SELECT messages FROM sessions WHERE slug=?", (slug,)).fetchone()
            if not source:
                return "Cannot activate memory: its source conversation was removed."
            try:
                evidence = {s.id: s.text for s in sources_from_messages(json.loads(source[0]))}
            except (ValueError, TypeError, KeyError):
                return "Cannot activate memory: its source conversation is unreadable."
            if not quote or quote not in evidence.get(source_id, ""):
                return "Cannot activate memory: its source evidence has changed."
            conn.execute("DELETE FROM memory WHERE id IN (SELECT memory_id FROM memory_candidates "
                         "WHERE topic=? AND status='active')", (topic,))
            conn.execute("UPDATE memory_candidates SET status='superseded',memory_id=NULL "
                         "WHERE topic=? AND status='active'", (topic,))
            memory_id = _activate(conn, content, candidate_id, db.utcnow())
            conn.execute("UPDATE memory_candidates SET status='active',memory_id=? WHERE id=?",
                         (memory_id, candidate_id))
            return f"Activated memory: {content}"
        if memory_id:
            conn.execute("DELETE FROM memory WHERE id=?", (memory_id,))
        conn.execute("UPDATE memory_candidates SET status='rejected',memory_id=NULL,content='',quote='',"
                     "source_id='',session_slug=NULL,topic='forgotten' WHERE id=?", (candidate_id,))
        return f"Rejected memory: {content}"


def job_status() -> str:
    state = "disabled" if not config.memory_enabled else "paused" if paused() else "enabled"
    lines = [f"Memory extraction: {state}; model {config.memory_model}; review {config.memory_review}.",
             f"Scan interval: {config.memory_scan_interval}s; job timeout: {config.memory_timeout}s."]
    for slug, status, attempts, error in db.query(
            "SELECT session_slug,status,attempts,last_error FROM memory_jobs ORDER BY updated_at DESC"):
        lines.append(f"  {slug} [{status}] attempts {attempts}" + (f" — {error}" if error else ""))
    return "\n".join(lines)


def show_candidate(prefix: str) -> str:
    """Show evidence for one candidate without exposing terminal control codes."""
    if not re.fullmatch(r"[a-fA-F0-9]+", prefix):
        return "Usage: /memory show <candidate-id or memory-id>"
    rows = db.query("SELECT id,content,status,topic,confidence,session_slug,source_id,quote "
                    "FROM memory_candidates WHERE id LIKE ? || '%' OR memory_id LIKE ? || '%'",
                    (prefix.lower(), prefix.lower()))
    if not rows:
        explicit = db.query("SELECT content FROM memory WHERE id LIKE ? || '%'", (prefix.lower(),))
        if len(explicit) == 1:
            return "Explicitly saved memory (no extraction evidence): " + json.dumps(explicit[0][0])
        if explicit:
            return "Ambiguous memory id; use more characters."
    if len(rows) != 1:
        return "No matching candidate." if not rows else "Ambiguous candidate id; use more characters."
    return json.dumps(asdict(SavedCandidate(*rows[0])), ensure_ascii=False, indent=2)


def retry_failed() -> None:
    db.execute("UPDATE memory_jobs SET status='pending',attempts=0,next_run_at='',last_error=NULL "
               "WHERE status='error'")


def run_worker(stop: threading.Event) -> None:
    """Drain eligible work at startup, then in periodic scans until shutdown."""
    extractor = ModelExtractor()
    try:
        while not stop.is_set():
            scan_before = db.utcnow()
            try:
                # Finish the eligible backlog instead of doing just one batch
                # per hour. Later conversation saves wait for the next cycle.
                while not stop.is_set() and process_one(extractor, scan_before=scan_before):
                    pass
            except db.StoreError as exc:
                print(f"  [memory] job storage unavailable ({type(exc).__name__})", file=sys.stderr)
            if stop.wait(config.memory_scan_interval):
                break
    finally:
        try:
            extractor.close()
        except Exception as exc:  # report cleanup without logging provider content
            print(f"  [memory] extractor cleanup failed ({type(exc).__name__})", file=sys.stderr)
        finally:
            db.close_connection()
