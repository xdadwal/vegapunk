"""Requests that overlap, against a real server.

FastAPI's test client sends one request at a time, which hides bugs that only show
when a server handles several at once (two tabs, or a page that fires two calls).
"""

import socket
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
import uvicorn

from vegapunk.app import create_app
from vegapunk.config import Settings
from vegapunk.db import connect

OK = {"username": "admin", "password": "correct horse"}
BAD = {"username": "admin", "password": "wrong"}


@pytest.fixture
def live(settings: Settings) -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def burst(n: int, fn) -> list[httpx.Response]:
    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(lambda _: fn(), range(n)))


def test_overlapping_reads_all_succeed(live):
    for _ in range(10):
        responses = burst(8, lambda: httpx.get(f"{live}/api/setup/status"))
        assert [r.status_code for r in responses] == [200] * 8


def test_simultaneous_setup_creates_exactly_one_admin(live, settings):
    responses = burst(10, lambda: httpx.post(f"{live}/api/setup/admin", json=OK))
    assert sorted(r.status_code for r in responses) == [201] + [409] * 9
    winner = next(r for r in responses if r.status_code == 201)
    assert httpx.get(f"{live}/api/me", cookies=winner.cookies).status_code == 200
    conn = connect(settings.db_path)
    assert conn.execute("SELECT count(*) FROM admin").fetchone()[0] == 1
    conn.close()


def test_sign_in_is_saved_before_the_response_arrives(live):
    httpx.post(f"{live}/api/setup/admin", json=OK)
    for _ in range(20):
        signed_in = httpx.post(f"{live}/api/auth/sign-in", json=OK)
        assert signed_in.status_code == 204
        assert httpx.get(f"{live}/api/me", cookies=signed_in.cookies).status_code == 200


def test_parallel_wrong_guesses_stop_at_the_limit(live, settings):
    httpx.post(f"{live}/api/setup/admin", json=OK)
    responses = burst(30, lambda: httpx.post(f"{live}/api/auth/sign-in", json=BAD))
    codes = sorted(r.status_code for r in responses)
    assert codes == [401] * 5 + [429] * 25
    conn = connect(settings.db_path)
    assert conn.execute("SELECT count(*) FROM sign_in_failures").fetchone()[0] == 5
    conn.close()


def test_sign_in_works_while_capture_is_writing(tmp_path):
    """Capture and requests share one database; neither may starve or break the other."""
    from vegapunk.capture.records import HostSample

    class Busy:
        name = "host"

        def collect(self, now):
            return [HostSample(1.0, 1, 2, 3, 4, now)]

    settings = Settings(
        data_dir=tmp_path / "d",
        static_dir=None,
        secure_cookies=False,
        capture=True,
        capture_interval=1,
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings, collectors=[Busy()]), port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.02)
    base = f"http://127.0.0.1:{port}"
    try:
        httpx.post(f"{base}/api/setup/admin", json=OK)
        codes = [
            r.status_code
            for r in burst(10, lambda: httpx.post(f"{base}/api/auth/sign-in", json=OK))
        ]
        assert codes == [204] * 10
    finally:
        server.should_exit = True
        thread.join(timeout=10)
