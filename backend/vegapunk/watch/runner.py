"""The runner: one asyncio task that evaluates the rules every capture interval, resolves
findings whose signal has cleared, and starts investigations when they are due.

Every limit is counted from the `investigations` table, where each attempt is recorded
before the model is called, so a restart or a crash loop cannot reset them. Investigations
run one at a time on one lock; SQLite is only touched through `asyncio.to_thread`.
"""

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from logpose import Agent

from vegapunk.ai import store
from vegapunk.ai.operator import Operator, default_make_agent
from vegapunk.ai.store import AiConfig
from vegapunk.config import Settings
from vegapunk.db import connect
from vegapunk.host.docker import DockerHost
from vegapunk.timefmt import z
from vegapunk.watch import findings, rules
from vegapunk.watch.findings import EvidenceLine, FindingDraft
from vegapunk.watch.investigate import (
    InvestigationError,
    investigate,
    investigation_agent_factory,
)
from vegapunk.watch.rules import Signal

log = logging.getLogger(__name__)

COOLDOWN = timedelta(hours=6)  # per key, after any attempt that did not fail
BACKOFF = timedelta(minutes=30)  # per key, after a failed attempt
REVIEW_QUIET = timedelta(hours=24)  # per key, after its finding was reviewed
DAILY_CAP = 20  # watch investigations in any 24 hours, across all keys
CLEAR_AFTER = timedelta(minutes=30)  # a signal silent this long has cleared
CHECK_KEY = "scheduled:check"
SCHEDULED = "scheduled:"
HOST = "host"
PROBLEM = "The check hit a problem."

Investigator = Callable[
    [AiConfig, Signal | None, list[dict], Collection[str]], Awaitable[list[FindingDraft]]
]


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class _Provider:
    config: AiConfig | None
    skipped: str | None  # "off" | "key_rejected" | None
    updated_at: str | None


@dataclass(frozen=True)
class _Seen:
    signals: list[Signal]
    open_rule_keys: set[str]
    provider: _Provider
    check: findings.CheckState


def _provider(conn: sqlite3.Connection, crypto_key: bytes) -> _Provider:
    loaded = store.load_ai(conn, crypto_key)
    if loaded is None:
        return _Provider(None, "off", None)
    row = conn.execute("SELECT updated_at FROM ai_settings WHERE id = 1").fetchone()
    updated = row["updated_at"] if row else None
    if loaded.key_rejected_at is not None:
        return _Provider(None, "key_rejected", updated)
    return _Provider(loaded.config, None, updated)


def _subjects(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT name FROM containers WHERE removed_at IS NULL")
    return sorted({r["name"] for r in rows} | {HOST})


class Runner:
    def __init__(
        self,
        settings: Settings,
        docker: DockerHost,
        crypto_key: bytes,
        started_at: datetime,
        make_agent: Callable[..., Agent] | None = None,
        clock: Callable[[], datetime] = utcnow,
        investigator: Investigator | None = None,
        start: bool = True,
    ) -> None:
        self._settings = settings
        self._docker = docker
        self._crypto_key = crypto_key
        self._started_at = started_at
        self._make_agent = make_agent or default_make_agent
        self._clock = clock
        self._investigator = investigator or self._investigate
        self._autostart = start
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._check_task: asyncio.Task | None = None
        self._first_tick: datetime | None = None
        self._last_fired: dict[str, datetime] = {}
        self._cleared_at: dict[str, datetime] = {}
        self._watch_error: str | None = None
        self._unsupported_for: str | None = None  # the provider settings' updated_at
        self._skipped: str | None = None

    # ---- database access from async code
    def _with_db(self, fn):
        conn = connect(self._settings.db_path)
        try:
            return fn(conn)
        finally:
            conn.close()

    async def _db(self, fn):
        return await asyncio.to_thread(self._with_db, fn)

    # ---- the loop
    def start(self) -> None:
        if not self._autostart or self._task is not None:
            return
        self._task = asyncio.get_running_loop().create_task(self._loop(), name="vegapunk-watch")

    @property
    def loop_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def stop(self) -> None:
        tasks = [t for t in (self._task, self._check_task) if t is not None]
        self._task = self._check_task = None
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("The watch hit a problem")
            await asyncio.sleep(self._settings.capture_interval)

    # ---- one pass
    def _see(self, conn: sqlite3.Connection, now: datetime) -> _Seen:
        signals = rules.evaluate(conn, now, self._settings.capture_interval)
        open_rule = {k for k in findings.open_keys(conn) if not k.startswith(SCHEDULED)}
        check = findings.load_check_state(conn)
        if check.last_run_at is None:
            # A fresh install: wait for the next check hour, so nothing runs before data.
            findings.set_check_last_run(conn, now)
            check = replace(check, last_run_at=now)
        return _Seen(signals, open_rule, _provider(conn, self._crypto_key), check)

    async def tick(self) -> None:
        now = self._clock()
        seen = await self._db(lambda c: self._see(c, now))
        first = self._first_tick is None
        if first:
            self._first_tick = now
        self._note_firing(seen, now, first)
        cleared = [
            k for k in seen.open_rule_keys if now - self._last_fired.get(k, now) >= CLEAR_AFTER
        ]
        if cleared:
            await self._db(lambda c: [findings.resolve(c, k, now) for k in cleared])

        config = self._usable(seen.provider)
        if config is None or self._lock.locked():
            return
        async with self._lock:
            for signal in seen.signals:
                if signal.key in seen.open_rule_keys:
                    continue
                if not await self._db(lambda c, s=signal: self._due(c, s.key, self._clock())):
                    continue
                if not await self._run(config, signal):
                    return  # the provider cannot be used: stop until it changes
            if self._check_due(seen.check, self._clock()):
                await self._run(config, None)

    def _note_firing(self, seen: _Seen, now: datetime, first: bool) -> None:
        fired = {s.key for s in seen.signals}
        for key in fired:
            if not first and key not in self._last_fired:
                self._cleared_at[key] = now  # it was not firing before: it returns now
            self._last_fired[key] = now
        for key in seen.open_rule_keys:
            # After a restart, an open finding's signal counts from now.
            self._last_fired.setdefault(key, now)
        for key, at in self._last_fired.items():
            if key not in fired and now - at >= CLEAR_AFTER:
                self._cleared_at[key] = now

    def _usable(self, provider: _Provider) -> AiConfig | None:
        """The provider config, or None with `_skipped` saying why."""
        if provider.skipped is not None:
            self._skipped = provider.skipped
            return None
        if self._unsupported_for is not None:
            if provider.updated_at == self._unsupported_for:
                self._skipped = "unsupported"
                return None
            self._unsupported_for = None
        self._skipped = None
        return provider.config

    def _due(self, conn: sqlite3.Connection, key: str, now: datetime) -> bool:
        last, failed, _ = findings.investigation_counts(conn, key, now)
        if last is not None:
            wait = BACKOFF if failed is not None and failed >= last else COOLDOWN
            if now - last < wait:
                return False
        reviewed = findings.last_reviewed_at(conn, key)
        if reviewed is not None:
            if now - reviewed < REVIEW_QUIET:
                return False
            if self._cleared_at.get(key, reviewed) <= reviewed:
                return False  # it has not cleared since the review
        return findings.watch_count(conn, now) < DAILY_CAP

    def _check_due(self, check: findings.CheckState, now: datetime) -> bool:
        if not check.enabled or check.last_run_at is None:
            return False
        if now - check.last_run_at > timedelta(hours=24):
            return True  # the server was down at the hour: catch up once
        mark = now.replace(hour=check.hour, minute=0, second=0, microsecond=0)
        # Only when this process was up at the hour; a later start waits for the catch-up.
        first = self._first_tick or now
        return first <= mark <= now and check.last_run_at < mark

    # ---- one investigation
    async def _run(self, config: AiConfig, focus: Signal | None) -> bool:
        """Run one investigation and store what it found. False when the provider turned
        out to be unusable (rejected key, no structured answers)."""
        key, trigger = (focus.key, "watch") if focus else (CHECK_KEY, "scheduled")
        now = self._clock()

        def begin(conn):
            if focus is None:
                findings.set_check_last_run(conn, now)
            run_id = findings.begin_investigation(
                conn, key, trigger, now, focus.marker if focus else None
            )
            return (
                run_id,
                findings.list_findings(conn, status="open", query=None),
                _subjects(conn),
            )

        run_id, open_now, subjects = await self._db(begin)
        try:
            drafts = await self._investigator(config, focus, open_now, subjects)
        except InvestigationError as exc:
            await self._failed(run_id, focus, exc.code, exc.message)
            return exc.code not in ("key_rejected", "unsupported")
        except Exception:
            log.exception("A check hit a problem")
            await self._failed(run_id, focus, "error", PROBLEM)
            return True

        items = self._keyed(drafts, focus)
        outcome = "finding" if items else "discarded" if drafts else "nothing"
        done = self._clock()

        def finish(conn):
            if items:
                findings.store_findings(conn, items, trigger=trigger, now=done)
            findings.finish_investigation(conn, run_id, outcome)
            if focus is None:
                findings.set_check_error(conn, None)

        await self._db(finish)
        self._watch_error = None
        return True

    @staticmethod
    def _keyed(drafts: list[FindingDraft], focus: Signal | None) -> list[tuple[str, FindingDraft]]:
        items = []
        for draft in drafts:
            if focus is not None and draft.subject == focus.subject:
                fact = EvidenceLine("metric", focus.summary, None)
                items.append((focus.key, replace(draft, evidence=(fact, *draft.evidence))))
            elif draft.evidence:
                items.append((f"{SCHEDULED}{draft.subject}", draft))
        return items

    async def _failed(self, run_id: int, focus: Signal | None, code: str, message: str) -> None:
        now = self._clock()

        def write(conn):
            findings.finish_investigation(conn, run_id, "failed", message)
            if focus is None:
                findings.set_check_error(conn, message)
            if code == "key_rejected":
                store.mark_key_rejected(conn, now)
            return _provider(conn, self._crypto_key).updated_at

        updated_at = await self._db(write)
        self._watch_error = message
        if code == "key_rejected":
            self._skipped = "key_rejected"
        elif code == "unsupported":
            self._unsupported_for = updated_at
            self._skipped = "unsupported"

    async def _investigate(
        self,
        config: AiConfig,
        focus: Signal | None,
        open_findings: list[dict],
        subjects: Collection[str],
    ) -> list[FindingDraft]:
        host = await self._db(Operator._host_facts)
        factory = investigation_agent_factory(
            config=config,
            settings=self._settings,
            docker=self._docker,
            started_at=self._started_at,
            host=host,
            make_agent=self._make_agent,
        )
        return await investigate(
            factory, focus=focus, open_findings=open_findings, subjects=subjects
        )

    # ---- for the API
    async def run_check_now(self) -> bool:
        """Start the daily check now, in the background. False when an investigation is
        already running or the provider cannot be used (`status()` says why)."""
        if self._lock.locked():
            return False
        provider = await self._db(lambda c: _provider(c, self._crypto_key))
        config = self._usable(provider)
        if config is None or self._lock.locked():
            return False
        await self._lock.acquire()  # free and unawaited above: taken at once

        async def check():
            try:
                await self._run(config, None)
            except Exception:
                log.exception("The check hit a problem")
            finally:
                self._lock.release()

        self._check_task = asyncio.get_running_loop().create_task(check())
        return True

    async def status(self) -> dict:
        def read(conn):
            return findings.load_check_state(conn), _provider(conn, self._crypto_key)

        check, provider = await self._db(read)
        self._usable(provider)
        return {
            "running": self._lock.locked(),
            "last_run_at": z(check.last_run_at),
            "last_error": self._watch_error or check.last_error,
            "skipped": self._skipped,
        }
