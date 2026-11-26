import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vegapunk.app import create_app
from vegapunk.capture.records import (
    ContainerList,
    ContainerObservation,
    ContainerSample,
    HostFacts,
    HostSample,
)
from vegapunk.capture.scheduler import Scheduler
from vegapunk.config import Settings
from vegapunk.db import connect
from vegapunk.host.docker import DockerError

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
SAMPLE = HostSample(5.0, 1, 2, 3, 4, T0)
LISTING = ContainerList((ContainerObservation("a", "web", "img", "running", None, 0, T0),))


class Fake:
    def __init__(self, name, result):
        self.name, self.result, self.calls = name, result, 0

    def collect(self, now):
        self.calls += 1
        result = self.result() if callable(self.result) else self.result
        if isinstance(result, BaseException):
            raise result
        return result


def status(db_path):
    conn = connect(db_path)
    try:
        return {r["source"]: dict(r) for r in conn.execute("SELECT * FROM capture_status")}
    finally:
        conn.close()


def scalar(db_path, sql):
    conn = connect(db_path)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def scheduler(db_path, *collectors, clock=lambda: T0):
    return Scheduler(db_path, collectors, interval=15, clock=clock)


def test_a_cycle_ingests_every_collector_and_records_success(db_path):
    host, docker = (
        Fake("host", [SAMPLE]),
        Fake("docker", [LISTING, ContainerSample("a", 1.0, 2, 3)]),
    )
    scheduler(db_path, host, docker).run_once()
    assert scalar(db_path, "SELECT count(*) FROM host_metrics") == 1
    assert scalar(db_path, "SELECT count(*) FROM container_metrics") == 1
    rows = status(db_path)
    assert set(rows) == {"host", "docker"}
    assert rows["docker"]["last_success_at"] == T0.isoformat() == rows["docker"]["last_attempt_at"]
    assert rows["docker"]["last_error"] is None


def test_a_failing_collector_does_not_stop_the_others(db_path):
    scheduler(
        db_path, Fake("docker", DockerError("Can't reach Docker")), Fake("host", [SAMPLE])
    ).run_once()
    assert scalar(db_path, "SELECT count(*) FROM host_metrics") == 1
    docker = status(db_path)["docker"]
    assert docker["last_error"] == "Can't reach Docker" and docker["last_success_at"] is None


def test_recovery_clears_the_error_and_keeps_stored_containers(db_path):
    state = {"result": [LISTING]}
    docker = Fake("docker", lambda: state["result"])
    now = {"t": T0}
    s = scheduler(db_path, docker, clock=lambda: now["t"])
    s.run_once()
    state["result"] = DockerError("down")
    now["t"] = T0 + timedelta(seconds=15)
    s.run_once()
    assert status(db_path)["docker"]["last_error"] == "down"
    assert scalar(db_path, "SELECT count(*) FROM containers WHERE removed_at IS NULL") == 1
    state["result"] = [LISTING]
    now["t"] = T0 + timedelta(seconds=30)
    s.run_once()
    assert status(db_path)["docker"]["last_error"] is None


def test_failure_after_success_keeps_the_earlier_success_time(db_path):
    state = {"result": [LISTING]}
    now = {"t": T0}
    s = scheduler(db_path, Fake("docker", lambda: state["result"]), clock=lambda: now["t"])
    s.run_once()
    state["result"] = DockerError("down")
    now["t"] = T0 + timedelta(seconds=15)
    s.run_once()
    row = status(db_path)["docker"]
    assert row["last_success_at"] == T0.isoformat()
    assert row["last_attempt_at"] == (T0 + timedelta(seconds=15)).isoformat()


def test_a_failure_never_marks_containers_removed(db_path):
    state = {"result": [LISTING]}
    s = scheduler(db_path, Fake("docker", lambda: state["result"]))
    s.run_once()
    state["result"] = DockerError("down")
    for _ in range(3):
        s.run_once()
    assert scalar(db_path, "SELECT count(*) FROM containers WHERE removed_at IS NULL") == 1


def test_an_ingest_failure_is_recorded_as_an_error_not_a_success(db_path):
    scheduler(db_path, Fake("host", ["not a record"])).run_once()
    row = status(db_path)["host"]
    assert row["last_error"].startswith("Couldn't store data")
    assert row["last_success_at"] is None


def test_run_once_never_raises(tmp_path):
    missing = tmp_path / "nowhere" / "vegapunk.db"
    scheduler(missing, Fake("host", [SAMPLE])).run_once()  # cannot even open the database


def test_a_collector_raising_anything_does_not_escape(db_path):
    scheduler(db_path, Fake("host", RuntimeError("boom")), Fake("docker", [LISTING])).run_once()
    assert status(db_path)["host"]["last_error"] == "boom"
    assert status(db_path)["docker"]["last_error"] is None


def test_thread_runs_cycles_and_stops_promptly(db_path):
    fake = Fake("host", [SAMPLE])
    s = Scheduler(db_path, [fake], interval=1)
    s.start()
    assert wait_for(lambda: fake.calls >= 2)
    started = time.monotonic()
    s.stop()
    assert time.monotonic() - started < 5
    calls = fake.calls
    time.sleep(0.3)
    assert fake.calls == calls


def test_a_slow_cycle_never_overlaps_the_next(db_path):
    running, overlaps = [0], []

    def slow():
        running[0] += 1
        if running[0] > 1:
            overlaps.append(1)
        time.sleep(0.25)
        running[0] -= 1
        return [SAMPLE]

    s = Scheduler(db_path, [Fake("host", slow)], interval=1)
    s.start()
    time.sleep(1.6)
    s.stop()
    assert overlaps == []


def test_capture_off_starts_no_thread(tmp_path):
    settings = Settings(data_dir=tmp_path / "d", static_dir=None, secure_cookies=False)
    app = create_app(settings)
    with TestClient(app):
        assert app.state.started_at.tzinfo is not None
        assert "vegapunk-capture" not in {t.name for t in threading.enumerate()}


def test_app_starts_and_stops_the_scheduler(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "d",
        static_dir=None,
        secure_cookies=False,
        capture=True,
        capture_interval=1,
    )
    collectors = [Fake("host", [SAMPLE]), Fake("docker", [LISTING])]
    with TestClient(create_app(settings, collectors=collectors)):
        assert wait_for(lambda: collectors[0].calls >= 1 and collectors[1].calls >= 1)
        assert "vegapunk-capture" in {t.name for t in threading.enumerate()}
    assert "vegapunk-capture" not in {t.name for t in threading.enumerate()}
    assert set(status(settings.db_path)) == {"host", "docker"}


def test_docker_down_at_startup_still_serves(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "d",
        static_dir=None,
        secure_cookies=False,
        capture=True,
        capture_interval=1,
        docker_url="unix:///nonexistent.sock",
        disk_path=tmp_path,
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/health").status_code == 200
        assert wait_for(lambda: "docker" in status(settings.db_path))
    row = status(settings.db_path)["docker"]
    assert "Can't reach Docker" in row["last_error"] and row["last_success_at"] is None
    assert status(settings.db_path)["host"]["last_error"] is None
    # facts are never invented when Docker cannot be read
    assert scalar(settings.db_path, "SELECT count(*) FROM containers") == 0


@pytest.mark.parametrize("name", ["host", "docker"])
def test_every_source_name_is_a_known_source(name):
    from vegapunk.capture.collectors import SOURCES

    assert name in SOURCES


def test_facts_record_type_is_accepted(db_path):
    scheduler(
        db_path, Fake("docker", [HostFacts("nas", "os", "k", "29", 4, 100), LISTING])
    ).run_once()
    assert scalar(db_path, "SELECT hostname FROM host") == "nas"
