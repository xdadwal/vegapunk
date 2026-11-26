"""The runner: when signals and the daily check call the model, and what is stored.

Most tests drive `tick` with a fake clock, fake signals and a fake investigator. The limits
(cooldown, cap, back-off) are read from the `investigations` table, so a new Runner on the
same database (a restart) keeps them.
"""

import asyncio
import json
import logging
import threading
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.agent_helpers import CRYPTO_KEY, FakeDocker, save_config
from tests.fake_openai import FakeOpenAI, create_app, serve
from tests.helpers import listing, obs
from vegapunk.ai import store
from vegapunk.app import create_app as create_vegapunk
from vegapunk.config import Settings
from vegapunk.db import connect
from vegapunk.watch import findings as findings_module
from vegapunk.watch import rules
from vegapunk.watch.findings import (
    EvidenceLine,
    FindingDraft,
    get_finding,
    list_findings,
    record_investigation,
    review,
    save_check_settings,
)
from vegapunk.watch.investigate import InvestigationError
from vegapunk.watch.rules import Signal
from vegapunk.watch.runner import Runner

T0 = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)
RESTART = Signal("restart_loop:web", "restart_loop", "web", "Restarted 4 times in 12 min.")
DB_MEM = Signal("high_memory:db", "high_memory", "db", "Memory at 95% of its 512 MiB limit.")
WEB_LINE = EvidenceLine("log", "database is locked", "2026-11-27T09:59:00Z")


class Clock:
    def __init__(self, at: datetime = T0) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def advance(self, **kw) -> datetime:
        self.at += timedelta(**kw)
        return self.at


def draft(subject="web", title="The database is locked", evidence=(WEB_LINE,)):
    return FindingDraft(subject, title, "diagnosis", "fix", tuple(evidence))


class Investigator:
    """Stands in for `investigate`. `answers` is a list of results or exceptions, used in
    order (the last one repeats); `gate` makes every call wait until it is set."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers) or [[]]
        self.calls: list[dict] = []
        self.active = 0
        self.most_active = 0
        self.gate: asyncio.Event | None = None
        self.entered: asyncio.Event | None = None

    async def __call__(self, config, focus, open_findings, subjects):
        self.calls.append(
            {"config": config, "focus": focus, "open": open_findings, "subjects": subjects}
        )
        self.active += 1
        self.most_active = max(self.most_active, self.active)
        try:
            if self.entered is not None:
                self.entered.set()
            await asyncio.sleep(0)
            if self.gate is not None:
                await self.gate.wait()
            answer = self.answers[min(len(self.calls) - 1, len(self.answers) - 1)]
            if isinstance(answer, BaseException):
                raise answer
            return list(answer)
        finally:
            self.active -= 1


class Signals:
    """What the rules see; tests change `now` between ticks."""

    def __init__(self, monkeypatch) -> None:
        self.now: list[Signal] = []
        monkeypatch.setattr(rules, "evaluate", lambda conn, now, interval=15: list(self.now))


@pytest.fixture
def signals(monkeypatch) -> Signals:
    return Signals(monkeypatch)


def db(settings, fn):
    conn = connect(settings.db_path)
    try:
        return fn(conn)
    finally:
        conn.close()


def rows(settings, sql, args=()):
    return db(settings, lambda c: [dict(r) for r in c.execute(sql, args)])


def check_row(settings) -> dict:
    return rows(settings, "SELECT * FROM check_settings WHERE id = 1")[0]


def set_check(settings, *, enabled=True, hour=2, last_run_at=None):
    def write(c):
        save_check_settings(c, enabled, hour)
        c.execute(
            "UPDATE check_settings SET last_run_at = ? WHERE id = 1",
            (last_run_at.isoformat() if last_run_at else None,),
        )

    db(settings, write)


@pytest.fixture
def world(settings, db_path):
    """Two containers, a saved provider, and the daily check switched off."""
    db(settings, lambda c: listing(c, T0, obs("a", "web"), obs("b", "db")))
    save_config(settings)
    set_check(settings, enabled=False)
    return settings


def runner(settings, investigator, clock, **kw) -> Runner:
    return Runner(
        settings,
        FakeDocker(),
        CRYPTO_KEY,
        T0,
        clock=clock,
        investigator=investigator,
        start=False,
        **kw,
    )


def run(coro):
    return asyncio.run(coro)


def outcomes(settings):
    return [
        (r["key"], r["trigger"], r["outcome"])
        for r in rows(settings, "SELECT * FROM investigations ORDER BY id")
    ]


def open_findings(settings):
    return db(settings, lambda c: list_findings(c, status="open", query=None))


# ---- signals start investigations, at most once per key
def test_a_firing_rule_investigates_once_and_not_on_the_next_100_ticks(world, signals):
    clock, inv = Clock(), Investigator([])
    signals.now = [RESTART]

    async def go():
        r = runner(world, inv, clock)
        for _ in range(101):
            await r.tick()
            clock.advance(seconds=15)

    run(go())
    assert len(inv.calls) == 1
    call = inv.calls[0]
    assert call["focus"] == RESTART
    assert call["config"].api_key is not None
    assert set(call["subjects"]) == {"web", "db", "host"}
    assert outcomes(world) == [("restart_loop:web", "watch", "nothing")]


def test_a_key_with_an_open_finding_never_starts_a_run(world, signals):
    clock, inv = Clock(), Investigator([draft()])
    signals.now = [RESTART]

    async def go():
        r = runner(world, inv, clock)
        await r.tick()
        clock.advance(hours=7)  # past the cooldown, but the finding is still open
        await r.tick()

    run(go())
    assert len(inv.calls) == 1
    assert [f["key"] for f in open_findings(world)] == ["restart_loop:web"]


def test_two_keys_in_one_tick_run_one_after_another(world, signals):
    clock, inv = Clock(), Investigator([])
    signals.now = [RESTART, DB_MEM]
    run(runner(world, inv, clock).tick())
    assert [c["focus"].key for c in inv.calls] == ["restart_loop:web", "high_memory:db"]
    assert inv.most_active == 1


def test_the_21st_watch_investigation_in_24_hours_is_skipped(world, signals):
    clock, inv = Clock(), Investigator([])
    signals.now = [Signal(f"high_memory:c{i}", "high_memory", f"c{i}", "high") for i in range(21)]

    async def go():
        r = runner(world, inv, clock)
        await r.tick()
        clock.advance(hours=7)  # cooldowns over, but the 20 are still inside 24 hours
        await r.tick()

    run(go())
    assert len(inv.calls) == 20
    assert "high_memory:c20" not in {c["focus"].key for c in inv.calls}


def test_the_cap_counts_only_watch_runs_across_all_keys(world, signals):
    def seed(c):
        for i in range(19):
            record_investigation(c, f"other:{i}", "watch", T0 - timedelta(hours=1), "nothing")
        for _ in range(5):
            record_investigation(c, "scheduled:check", "scheduled", T0, "nothing")

    db(world, seed)
    clock, inv = Clock(), Investigator([])
    signals.now = [RESTART, DB_MEM]
    run(runner(world, inv, clock).tick())
    assert [c["focus"].key for c in inv.calls] == ["restart_loop:web"]


def test_a_failed_investigation_backs_off_30_minutes_and_counts_toward_the_cap(world, signals):
    def seed(c):
        for i in range(18):
            record_investigation(c, f"other:{i}", "watch", T0 - timedelta(hours=1), "nothing")

    db(world, seed)
    clock = Clock()
    inv = Investigator(InvestigationError("provider_error", "The AI provider couldn't answer: 500"))
    signals.now = [RESTART]

    async def go():
        r = runner(world, inv, clock)
        await r.tick()
        clock.advance(minutes=29)
        await r.tick()
        assert len(inv.calls) == 1
        clock.advance(minutes=1)
        await r.tick()  # 30 minutes on: retried, and that is attempt 20
        assert len(inv.calls) == 2
        signals.now = [RESTART, DB_MEM]
        clock.advance(minutes=30)
        await r.tick()  # the cap is reached: neither runs
        return await r.status()

    status = run(go())
    assert len(inv.calls) == 2
    assert [o for o in outcomes(world) if o[0] == "restart_loop:web"] == [
        ("restart_loop:web", "watch", "failed")
    ] * 2
    errors = rows(world, "SELECT error FROM investigations WHERE key = 'restart_loop:web'")
    assert errors[0]["error"] == "The AI provider couldn't answer: 500"
    assert status["last_error"] == "The AI provider couldn't answer: 500"


def test_after_an_answer_the_key_waits_6_hours(world, signals):
    clock, inv = Clock(), Investigator([])
    signals.now = [RESTART]

    async def go():
        r = runner(world, inv, clock)
        await r.tick()
        clock.advance(hours=5, minutes=59)
        await r.tick()
        assert len(inv.calls) == 1
        clock.advance(minutes=1)
        await r.tick()

    run(go())
    assert len(inv.calls) == 2


def test_a_restart_keeps_the_cooldown_and_back_off(world, signals):
    clock = Clock()
    signals.now = [RESTART, DB_MEM]
    inv = Investigator([], InvestigationError("timeout", "The check took too long."))
    run(runner(world, inv, clock).tick())
    assert len(inv.calls) == 2

    clock.advance(minutes=10)
    again = Investigator([])
    run(runner(world, again, clock).tick())  # a new process on the same database
    assert again.calls == []
    clock.advance(minutes=20)
    run(runner(world, again, clock).tick())
    assert [c["focus"].key for c in again.calls] == ["high_memory:db"]  # only the failed one


def test_an_interrupted_run_counts_as_failed_after_a_crash(world, signals):
    """The attempt is recorded before the model is called, so a crash loop cannot call it
    every 15 seconds."""
    clock, inv = Clock(), Investigator([])
    inv.gate = asyncio.Event()  # never set: the "process" dies mid-run
    signals.now = [RESTART]

    async def go():
        r = runner(world, inv, clock)
        task = asyncio.create_task(r.tick())
        while not inv.calls:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(go())
    assert outcomes(world) == [("restart_loop:web", "watch", "failed")]
    clock.advance(minutes=1)
    again = Investigator([])
    run(runner(world, again, clock).tick())
    assert again.calls == []


# ---- what is stored
def test_the_signal_subjects_finding_has_the_rule_key_and_fact_line_first(world, signals):
    clock = Clock()
    other = draft("db", "The database is slow", (EvidenceLine("log", "slow query 9s", None),))
    inv = Investigator([draft(), other])
    signals.now = [RESTART]
    run(runner(world, inv, clock).tick())

    found = {f["key"]: f for f in open_findings(world)}
    assert set(found) == {"restart_loop:web", "scheduled:db"}
    web = db(world, lambda c: get_finding(c, found["restart_loop:web"]["id"]))
    assert web["trigger"] == "watch"
    assert [(e["source"], e["text"]) for e in web["evidence"]] == [
        ("metric", "Restarted 4 times in 12 min."),
        ("log", "database is locked"),
    ]
    dbf = db(world, lambda c: get_finding(c, found["scheduled:db"]["id"]))
    assert [e["source"] for e in dbf["evidence"]] == ["log"]
    assert outcomes(world) == [("restart_loop:web", "watch", "finding")]


def test_the_signal_subject_keeps_its_finding_with_only_the_fact_line(world, signals):
    clock, inv = Clock(), Investigator([draft(evidence=())])
    signals.now = [RESTART]
    run(runner(world, inv, clock).tick())
    (finding,) = open_findings(world)
    full = db(world, lambda c: get_finding(c, finding["id"]))
    assert [e["source"] for e in full["evidence"]] == ["metric"]


def test_drafts_with_no_evidence_left_are_discarded(world, signals):
    clock, inv = Clock(), Investigator([draft("db", evidence=())])
    signals.now = [RESTART]
    run(runner(world, inv, clock).tick())
    assert open_findings(world) == []
    assert outcomes(world) == [("restart_loop:web", "watch", "discarded")]


def test_an_oom_signal_records_its_marker(world, signals):
    oom = Signal("oom_kill:web", "oom_kill", "web", "Killed for running out of memory.", "m1")
    clock, inv = Clock(), Investigator([])
    signals.now = [oom]
    run(runner(world, inv, clock).tick())
    assert rows(world, "SELECT marker FROM investigations") == [{"marker": "m1"}]


def test_open_findings_are_passed_to_the_investigation(world, signals):
    clock, inv = Clock(), Investigator([draft()], [])
    signals.now = [RESTART]

    async def go():
        r = runner(world, inv, clock)
        await r.tick()
        signals.now = [DB_MEM]
        await r.tick()

    run(go())
    assert [f["key"] for f in inv.calls[1]["open"]] == ["restart_loop:web"]


# ---- resolution
def test_a_signal_absent_for_30_minutes_of_ticks_resolves_its_finding(world, signals):
    clock, inv = Clock(), Investigator([draft()])
    signals.now = [RESTART]

    async def go():
        r = runner(world, inv, clock)
        await r.tick()
        signals.now = []
        clock.advance(seconds=15)
        await r.tick()  # the first tick that sees it gone
        clock.advance(minutes=10)
        await r.tick()
        assert len(open_findings(world)) == 1  # 10 minutes is not enough
        clock.advance(minutes=19, seconds=45)
        await r.tick()
        assert len(open_findings(world)) == 1  # 29 min 45 s of observed absence
        clock.advance(seconds=15)
        await r.tick()

    run(go())
    assert open_findings(world) == []
    assert rows(world, "SELECT status FROM findings") == [{"status": "resolved"}]


def test_a_long_batch_of_investigations_does_not_count_as_absence(world, signals):
    """Ticks stop while a batch runs; a signal still firing when the batch began must not
    be resolved just because the next tick comes 50 minutes later."""
    clock = Clock()
    signals.now = [RESTART]
    inv = Investigator([draft()])

    async def go():
        r = runner(world, inv, clock)
        await r.tick()  # restart_loop:web now has an open finding
        signals.now = [RESTART, DB_MEM]
        clock.advance(seconds=15)

        async def slow(config, focus, open_findings, subjects):
            clock.advance(minutes=50)  # one long investigation
            return []

        r._investigator = slow
        await r.tick()  # web still fires; the db run takes 50 min
        signals.now = []
        clock.advance(seconds=15)
        await r.tick()  # 50 min since web last fired, but the first tick that sees it gone
        assert len(open_findings(world)) == 1
        clock.advance(minutes=30)
        await r.tick()

    run(go())
    assert open_findings(world) == []


def test_a_signal_gone_after_an_earlier_run_in_the_tick_is_skipped(world, signals):
    clock = Clock()
    signals.now = [RESTART, DB_MEM]
    calls = []

    async def first_run_clears_db(config, focus, open_findings, subjects):
        calls.append(focus.key)
        signals.now = [RESTART]  # the db's memory came down while web was looked at
        return []

    run(runner(world, first_run_clears_db, clock).tick())
    assert calls == ["restart_loop:web"]
    assert outcomes(world) == [("restart_loop:web", "watch", "nothing")]


def test_a_signal_that_keeps_firing_keeps_its_finding_open(world, signals):
    clock, inv = Clock(), Investigator([draft()])
    signals.now = [RESTART]

    async def go():
        r = runner(world, inv, clock)
        for _ in range(5):
            await r.tick()
            clock.advance(minutes=20)

    run(go())
    assert len(open_findings(world)) == 1


def test_a_restart_neither_resolves_at_once_nor_forgets_open_findings(world, signals):
    clock, inv = Clock(), Investigator([draft()])
    signals.now = [RESTART]
    run(runner(world, inv, clock).tick())

    signals.now = []
    clock.advance(hours=2)
    r = runner(world, Investigator([]), clock)
    run(r.tick())  # first tick of a new process: the clock starts now
    assert len(open_findings(world)) == 1
    clock.advance(minutes=30)
    run(r.tick())
    assert open_findings(world) == []


def test_scheduled_findings_are_never_resolved_by_the_runner(world, signals):
    clock = Clock()
    set_check(world, enabled=True, last_run_at=T0 - timedelta(hours=30))
    inv = Investigator([draft()])

    async def go():
        r = runner(world, inv, clock)
        await r.tick()
        clock.advance(hours=3)
        await r.tick()

    run(go())
    assert [f["key"] for f in open_findings(world)] == ["scheduled:web"]


def test_a_reviewed_findings_key_stays_quiet_until_the_cooldown_ends(world, signals):
    clock, inv = Clock(), Investigator([draft()], [])
    signals.now = [RESTART]

    async def go():
        r = runner(world, inv, clock)
        await r.tick()
        (finding,) = open_findings(world)
        db(world, lambda c: review(c, finding["id"], clock()))
        for _ in range(12):  # firing all along, past 24 hours
            clock.advance(hours=2, minutes=1)
            await r.tick()
        assert len(inv.calls) == 1
        signals.now = []  # it clears once ...
        clock.advance(minutes=1)
        await r.tick()
        clock.advance(minutes=30)
        await r.tick()
        signals.now = [RESTART]  # ... and returns
        clock.advance(minutes=1)
        await r.tick()

    run(go())
    assert len(inv.calls) == 2


def test_a_reviewed_key_that_clears_and_returns_within_24_hours_stays_quiet(world, signals):
    clock, inv = Clock(), Investigator([draft()], [])
    signals.now = [RESTART]

    async def go():
        r = runner(world, inv, clock)
        await r.tick()
        (finding,) = open_findings(world)
        db(world, lambda c: review(c, finding["id"], clock()))
        signals.now = []
        clock.advance(hours=1)
        await r.tick()
        signals.now = [RESTART]
        clock.advance(hours=6)  # past the 6 h cooldown, inside the 24 h after review
        await r.tick()

    run(go())
    assert len(inv.calls) == 1


# ---- the daily check
def test_on_a_fresh_install_the_first_tick_records_the_time_and_runs_nothing(world, signals):
    set_check(world, enabled=True, hour=10)
    clock, inv = Clock(T0 + timedelta(minutes=5)), Investigator([])
    run(runner(world, inv, clock).tick())
    assert inv.calls == []
    assert datetime.fromisoformat(check_row(world)["last_run_at"]) == clock()


def test_the_daily_check_runs_at_the_first_tick_at_or_after_the_hour_once_a_day(world, signals):
    set_check(world, enabled=True, hour=2, last_run_at=T0 - timedelta(hours=10))  # 00:00
    clock, inv = Clock(T0.replace(hour=1, minute=59, second=50)), Investigator([])

    async def go():
        r = runner(world, inv, clock)
        await r.tick()
        assert inv.calls == []
        clock.at = T0.replace(hour=2, minute=0, second=5)
        await r.tick()
        assert len(inv.calls) == 1 and inv.calls[0]["focus"] is None
        for _ in range(10):
            clock.advance(minutes=15)
            await r.tick()
        assert len(inv.calls) == 1
        clock.at = T0.replace(hour=2, minute=0, second=10) + timedelta(days=1)
        await r.tick()

    run(go())
    assert len(inv.calls) == 2
    assert outcomes(world) == [("scheduled:check", "scheduled", "nothing")] * 2


def test_the_daily_check_stores_scheduled_findings_without_a_fact_line(world, signals):
    set_check(world, enabled=True, last_run_at=T0 - timedelta(hours=30))
    clock, inv = Clock(), Investigator([draft()])
    run(runner(world, inv, clock).tick())
    (finding,) = open_findings(world)
    full = db(world, lambda c: get_finding(c, finding["id"]))
    assert (full["key"], full["trigger"]) == ("scheduled:web", "scheduled")
    assert [e["source"] for e in full["evidence"]] == ["log"]


@pytest.mark.parametrize("age_hours,runs", [(25, 1), (5, 0)])
def test_a_server_started_after_the_hour_catches_up_only_after_24_hours(
    world, signals, age_hours, runs
):
    start = T0.replace(hour=5)  # 3 hours after the 02:00 check
    set_check(world, enabled=True, hour=2, last_run_at=start - timedelta(hours=age_hours))
    clock, inv = Clock(start), Investigator([])

    async def go():
        r = runner(world, inv, clock)
        await r.tick()
        clock.advance(minutes=15)
        await r.tick()

    run(go())
    assert len(inv.calls) == runs


def test_last_run_at_is_written_when_the_check_starts(world, signals):
    set_check(world, enabled=True, last_run_at=T0 - timedelta(hours=30))
    clock, inv = Clock(), Investigator([])
    inv.gate = asyncio.Event()
    seen = {}

    async def go():
        r = runner(world, inv, clock)
        task = asyncio.create_task(r.tick())
        while not inv.calls:
            await asyncio.sleep(0.01)
        seen["at"] = check_row(world)["last_run_at"]
        await r.stop()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(go())
    assert datetime.fromisoformat(seen["at"]) == T0


def test_an_interrupted_daily_check_leaves_an_error(world, signals):
    set_check(world, enabled=True, last_run_at=T0 - timedelta(hours=30))
    clock, inv = Clock(), Investigator([])
    inv.gate = asyncio.Event()

    async def go():
        r = Runner(world, FakeDocker(), CRYPTO_KEY, T0, clock=clock, investigator=inv, start=True)
        r.start()
        while not inv.calls:
            await asyncio.sleep(0.01)
        await r.stop()
        return await r.status()

    status = run(go())
    assert check_row(world)["last_error"] == "The check was interrupted."
    assert status["last_error"] == "The check was interrupted."


def test_a_check_run_now_during_a_tick_is_not_run_again_by_that_tick(world, signals, monkeypatch):
    clock, inv = Clock(), Investigator([draft()], [])
    signals.now = [RESTART]
    run(runner(world, inv, clock).tick())  # an open finding to resolve later
    entered, release = threading.Event(), threading.Event()
    real = findings_module.resolve

    def slow_resolve(conn, key, now):
        entered.set()
        release.wait(5)
        real(conn, key, now)

    monkeypatch.setattr(findings_module, "resolve", slow_resolve)

    async def go():
        r = runner(world, inv, clock)
        signals.now = []
        await r.tick()  # absence starts
        set_check(world, enabled=True, last_run_at=T0 - timedelta(hours=30))  # now due
        clock.advance(minutes=30)
        task = asyncio.create_task(r.tick())  # resolves, so it waits in its first hops
        while not entered.is_set():
            await asyncio.sleep(0.01)
        assert await r.run_check_now() is True
        while (await r.status())["running"]:
            await asyncio.sleep(0.01)
        release.set()
        await task

    run(go())
    assert [o for o in outcomes(world) if o[0] == "scheduled:check"] == [
        ("scheduled:check", "scheduled", "nothing")
    ]


def test_a_failed_daily_check_stores_its_error_until_the_next_success(world, signals):
    set_check(world, enabled=True, last_run_at=T0 - timedelta(hours=30))
    clock = Clock()
    inv = Investigator(InvestigationError("timeout", "The check took too long."), [])

    async def go():
        r = runner(world, inv, clock)
        await r.tick()
        assert check_row(world)["last_error"] == "The check took too long."
        assert (await r.status())["last_error"] == "The check took too long."
        clock.advance(days=1)
        await r.tick()
        return await r.status()

    status = run(go())
    assert check_row(world)["last_error"] is None and status["last_error"] is None
    assert len(inv.calls) == 2


def test_disabled_checks_do_not_run_but_rules_still_do(world, signals):
    set_check(world, enabled=False, last_run_at=T0 - timedelta(hours=30))
    clock, inv = Clock(), Investigator([])
    signals.now = [RESTART]
    run(runner(world, inv, clock).tick())
    assert [c["focus"] for c in inv.calls] == [RESTART]


# ---- the provider
def test_no_provider_means_skipped_off_and_nothing_called(settings, db_path, signals):
    db(settings, lambda c: listing(c, T0, obs("a", "web")))
    set_check(settings, enabled=True, last_run_at=T0 - timedelta(hours=30))
    clock, inv = Clock(), Investigator([])
    signals.now = [RESTART]

    async def go():
        r = runner(settings, inv, clock)
        await r.tick()
        return await r.status()

    status = run(go())
    assert inv.calls == [] and status["skipped"] == "off" and status["running"] is False
    assert outcomes(settings) == []


def test_a_key_rejected_during_a_run_is_marked_and_skips_until_a_key_is_saved(world, signals):
    clock = Clock()
    inv = Investigator(InvestigationError("key_rejected", "Your AI provider rejected the API key."))
    signals.now = [RESTART, DB_MEM]

    async def go():
        r = runner(world, inv, clock)
        await r.tick()
        assert len(inv.calls) == 1  # the second key is not tried with a rejected key
        loaded = db(world, lambda c: store.load_ai(c, CRYPTO_KEY))
        assert loaded.key_rejected_at is not None
        assert (await r.status())["skipped"] == "key_rejected"
        clock.advance(hours=7)
        await r.tick()
        assert len(inv.calls) == 1
        save_config(world, api_key="sk-a-new-key-0987654321")
        inv.answers = [[]]
        clock.advance(minutes=1)
        await r.tick()
        return await r.status()

    status = run(go())
    assert status["skipped"] is None
    assert len(inv.calls) == 3


def test_an_unsupported_provider_is_skipped_until_its_settings_change(world, signals):
    clock = Clock()
    inv = Investigator(InvestigationError("unsupported", "No structured answers."))
    signals.now = [RESTART]

    async def go():
        r = runner(world, inv, clock)
        await r.tick()
        clock.advance(hours=7)
        await r.tick()
        status = await r.status()
        assert len(inv.calls) == 1
        assert status["skipped"] == "unsupported" and status["last_error"]
        save_config(world, model="another-model", now=T0 + timedelta(hours=8))
        inv.answers = [[]]
        await r.tick()
        return await r.status()

    status = run(go())
    assert len(inv.calls) == 2 and status["skipped"] is None


def test_a_watch_error_is_cleared_by_the_next_success(world, signals):
    clock = Clock()
    inv = Investigator(InvestigationError("provider_error", "down"), [])
    signals.now = [RESTART]

    async def go():
        r = runner(world, inv, clock)
        await r.tick()
        assert (await r.status())["last_error"] == "down"
        clock.advance(minutes=30)
        await r.tick()
        return await r.status()

    assert run(go())["last_error"] is None


def test_an_unexpected_investigator_error_is_a_failed_attempt(world, signals, caplog):
    clock, inv = Clock(), Investigator(RuntimeError("boom"))
    signals.now = [RESTART]
    with caplog.at_level(logging.ERROR):
        run(runner(world, inv, clock).tick())
    assert outcomes(world) == [("restart_loop:web", "watch", "failed")]
    assert rows(world, "SELECT error FROM investigations")[0]["error"] == (
        "The check hit a problem."
    )


# ---- run now, concurrency, stopping
def test_run_check_now_runs_regardless_of_the_hour_and_refuses_while_busy(world, signals):
    set_check(world, enabled=True, hour=2, last_run_at=T0 - timedelta(hours=1))
    clock, inv = Clock(), Investigator([draft()])
    inv.gate = asyncio.Event()

    async def go():
        r = runner(world, inv, clock)
        assert await r.run_check_now() is True
        while not inv.calls:
            await asyncio.sleep(0.01)
        assert (await r.status())["running"] is True
        assert await r.run_check_now() is False
        signals.now = [RESTART]
        await r.tick()  # an investigation is running: the tick does not start another
        assert len(inv.calls) == 1
        inv.gate.set()
        while (await r.status())["running"]:
            await asyncio.sleep(0.01)
        await r.stop()

    run(go())
    assert datetime.fromisoformat(check_row(world)["last_run_at"]) == T0
    assert outcomes(world) == [("scheduled:check", "scheduled", "finding")]
    assert [f["key"] for f in open_findings(world)] == ["scheduled:web"]


def test_run_check_now_during_a_watch_run_returns_false(world, signals):
    clock, inv = Clock(), Investigator([])
    inv.gate = asyncio.Event()
    signals.now = [RESTART]

    async def go():
        r = runner(world, inv, clock)
        task = asyncio.create_task(r.tick())
        while not inv.calls:
            await asyncio.sleep(0.01)
        refused = await r.run_check_now()
        inv.gate.set()
        await task
        return refused

    assert run(go()) is False


def test_run_check_now_without_a_provider_returns_false(settings, db_path, signals):
    inv = Investigator([])
    assert run(runner(settings, inv, Clock()).run_check_now()) is False
    assert inv.calls == []


def test_stop_during_an_investigation_ends_it_and_writes_no_half_finding(world, signals):
    clock, inv = Clock(), Investigator([draft()])
    inv.gate = asyncio.Event()
    signals.now = [RESTART]

    async def go():
        r = Runner(world, FakeDocker(), CRYPTO_KEY, T0, clock=clock, investigator=inv, start=True)
        r.start()
        while not inv.calls:
            await asyncio.sleep(0.01)
        await asyncio.wait_for(r.stop(), 5)
        await r.stop()  # twice is fine
        inv.gate.set()
        await asyncio.sleep(0.05)

    run(go())
    assert rows(world, "SELECT * FROM findings") == []
    assert outcomes(world) == [("restart_loop:web", "watch", "failed")]


def test_a_crash_inside_tick_is_logged_and_the_loop_continues(world, signals, caplog):
    fast = Settings(
        data_dir=world.data_dir, static_dir=None, secure_cookies=False, capture_interval=0
    )
    calls = []

    async def go():
        r = Runner(fast, FakeDocker(), CRYPTO_KEY, T0, investigator=Investigator([]), start=True)

        async def tick():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("tick exploded")

        r.tick = tick
        r.start()
        while len(calls) < 3:
            await asyncio.sleep(0.01)
        await r.stop()

    with caplog.at_level(logging.ERROR):
        run(asyncio.wait_for(go(), 5))
    assert len(calls) >= 3
    assert "tick exploded" in caplog.text


def test_start_does_nothing_when_built_with_start_false(world):
    async def go():
        r = runner(world, Investigator([]), Clock())
        r.start()
        await asyncio.sleep(0.05)
        await r.stop()
        return r

    run(go())
    assert outcomes(world) == []


# ---- through the real investigation, against a fake OpenAI server
T1 = "2026-11-27T09:59:01.000000000Z"


def logs_call(name):
    return {
        "tool_calls": [
            {"id": "call_1", "name": "container_logs", "arguments": {"name": name, "lines": 50}}
        ]
    }


def answer(*findings):
    return {"text": [json.dumps({"findings": list(findings)})]}


def reported(subject, lines):
    return {
        "subject": subject,
        "title": "The database is locked",
        "diagnosis": "It keeps crashing.",
        "log_lines": lines,
        "suggested_fix": "Check the volume.",
    }


def test_a_signal_run_through_the_real_investigation_stores_verified_evidence(world, signals):
    fake = FakeOpenAI(
        script=[logs_call("web"), answer(reported("web", ["database is locked", "made up line"]))]
    )
    docker = FakeDocker({"a": [f"{T1} database is locked"]})
    signals.now = [RESTART]
    with serve(create_app(fake)) as url:
        save_config(world, base_url=f"{url}/v1")
        r = Runner(world, docker, CRYPTO_KEY, T0, clock=Clock(), start=False)
        run(r.tick())
    (finding,) = open_findings(world)
    full = db(world, lambda c: get_finding(c, finding["id"]))
    assert full["key"] == "restart_loop:web"
    assert [(e["source"], e["text"]) for e in full["evidence"]] == [
        ("metric", "Restarted 4 times in 12 min."),
        ("log", "database is locked"),
    ]
    assert outcomes(world) == [("restart_loop:web", "watch", "finding")]


def test_a_key_rejected_by_the_real_provider_is_marked(world, signals):
    fake = FakeOpenAI(script=[answer()], required_key="sk-some-other-key-999")
    signals.now = [RESTART]
    with serve(create_app(fake)) as url:
        save_config(world, base_url=f"{url}/v1")
        r = Runner(world, FakeDocker(), CRYPTO_KEY, T0, clock=Clock(), start=False)
        run(r.tick())
        status = run(r.status())
    assert status["skipped"] == "key_rejected"
    assert db(world, lambda c: store.load_ai(c, CRYPTO_KEY)).key_rejected_at is not None
    error = rows(world, "SELECT error FROM investigations")[0]["error"]
    assert "sk-" not in error


# ---- the app
def test_the_app_has_no_runner_without_capture(settings):
    app = create_vegapunk(settings)
    with TestClient(app):
        assert app.state.runner is None


def test_the_app_starts_and_stops_the_runner_with_capture(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        static_dir=None,
        secure_cookies=False,
        capture=True,
        capture_interval=1,
    )
    app = create_vegapunk(settings, collectors=[], docker=FakeDocker())
    with TestClient(app):
        runner_ = app.state.runner
        assert isinstance(runner_, Runner)
        assert runner_.loop_running
    assert not runner_.loop_running
