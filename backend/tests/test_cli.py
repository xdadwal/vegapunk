import pytest

from vegapunk.__main__ import main

OK = {"username": "admin", "password": "correct horse"}


@pytest.fixture(autouse=True)
def data_dir(settings, monkeypatch):
    monkeypatch.setenv("VEGAPUNK_DATA_DIR", str(settings.data_dir))


def entries(*values):
    it = iter(values)
    return lambda _prompt: next(it)


def test_reset_password_sets_it_and_signs_everyone_out(signed_in):
    assert main(["reset-password"], read_password=entries("brand new pass", "brand new pass")) == 0
    assert signed_in.get("/api/me").status_code == 401
    r = signed_in.post(
        "/api/auth/sign-in", json={"username": "admin", "password": "brand new pass"}
    )
    assert r.status_code == 204


def test_reset_password_clears_lockout(admin_client):
    for _ in range(5):
        admin_client.post("/api/auth/sign-in", json={"username": "admin", "password": "wrong"})
    assert main(["reset-password"], read_password=entries("brand new pass", "brand new pass")) == 0
    r = admin_client.post(
        "/api/auth/sign-in", json={"username": "admin", "password": "brand new pass"}
    )
    assert r.status_code == 204


def test_reset_password_without_admin_fails(client, capsys):
    assert main(["reset-password"], read_password=entries("brand new pass", "brand new pass")) == 1
    assert "create" in capsys.readouterr().err.lower()


def test_reset_password_with_no_database_fails_without_creating_one(settings, capsys):
    assert main(["reset-password"], read_password=entries("brand new pass", "brand new pass")) == 1
    assert not settings.db_path.exists()


def test_reset_password_mismatch_fails(signed_in, capsys):
    assert main(["reset-password"], read_password=entries("brand new pass", "different pass")) == 1
    assert "don't match" in capsys.readouterr().err
    signed_in.post("/api/auth/sign-out")
    assert signed_in.post("/api/auth/sign-in", json=OK).status_code == 204


def test_reset_password_too_short_fails(signed_in, capsys):
    assert main(["reset-password"], read_password=entries("short", "short")) == 1
    assert "at least 10" in capsys.readouterr().err
