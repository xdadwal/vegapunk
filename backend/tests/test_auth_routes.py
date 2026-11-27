from fastapi.testclient import TestClient

from vegapunk.app import create_app
from vegapunk.config import Settings

OK = {"username": "admin", "password": "correct horse"}
BAD = {"username": "admin", "password": "wrong"}


def test_me_requires_sign_in(admin_client):
    assert admin_client.get("/api/me").status_code == 401


def test_sign_in_then_me_then_sign_out(admin_client):
    r = admin_client.post("/api/auth/sign-in", json=OK)
    assert r.status_code == 204
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert admin_client.get("/api/me").json()["username"] == "admin"
    assert admin_client.post("/api/auth/sign-out").status_code == 204
    assert admin_client.get("/api/me").status_code == 401


def test_sign_out_without_a_session_still_succeeds(admin_client):
    assert admin_client.post("/api/auth/sign-out").status_code == 204


def test_wrong_password_and_unknown_user_look_the_same(admin_client):
    a = admin_client.post("/api/auth/sign-in", json=BAD)
    b = admin_client.post("/api/auth/sign-in", json={"username": "nobody", "password": "wrong"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_sixth_try_is_paused_even_with_the_right_password(admin_client):
    for _ in range(5):
        admin_client.post("/api/auth/sign-in", json=BAD)
    r = admin_client.post("/api/auth/sign-in", json=OK)
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0
    assert "locked_until" in r.json()


def test_paused_attempts_do_not_extend_the_pause(admin_client, conn):
    for _ in range(5):
        admin_client.post("/api/auth/sign-in", json=BAD)
    admin_client.post("/api/auth/sign-in", json=OK)
    assert conn.execute("SELECT count(*) FROM sign_in_failures").fetchone()[0] == 5


def test_success_clears_earlier_failures(admin_client, conn):
    admin_client.post("/api/auth/sign-in", json=BAD)
    admin_client.post("/api/auth/sign-in", json=OK)
    assert conn.execute("SELECT count(*) FROM sign_in_failures").fetchone()[0] == 0


def test_garbage_cookie_is_401(admin_client):
    admin_client.cookies.set("vegapunk_session", "garbage")
    assert admin_client.get("/api/me").status_code == 401


def test_setup_signs_you_in(client):
    client.post("/api/setup/admin", json=OK)
    assert client.get("/api/me").status_code == 200


def test_secure_flag_follows_setting(tmp_path):
    settings = Settings(data_dir=tmp_path / "d", static_dir=None, secure_cookies=True)
    with TestClient(create_app(settings), base_url="https://testserver") as c:
        r = c.post("/api/setup/admin", json=OK)
    assert "secure" in r.headers["set-cookie"].lower()
