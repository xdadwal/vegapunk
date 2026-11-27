import pytest
from fastapi.testclient import TestClient

from vegapunk.app import create_app

OK = {"username": "admin", "password": "correct horse"}


def test_update_display_name_and_username(signed_in):
    r = signed_in.patch("/api/me", json={"display_name": "Akira", "username": "akira"})
    assert r.status_code == 200
    assert r.json()["display_name"] == "Akira"
    assert signed_in.get("/api/me").json()["username"] == "akira"


def test_display_name_is_trimmed(signed_in):
    r = signed_in.patch("/api/me", json={"display_name": "  Akira  "})
    assert r.json()["display_name"] == "Akira"


@pytest.mark.parametrize(
    "body", [{"username": "Bad Name"}, {"display_name": "  "}, {"display_name": "x" * 65}]
)
def test_update_rejects_invalid(signed_in, body):
    assert signed_in.patch("/api/me", json=body).status_code == 422


def test_change_password(signed_in):
    r = signed_in.post(
        "/api/me/password",
        json={"current_password": "correct horse", "new_password": "battery staple"},
    )
    assert r.status_code == 204
    assert signed_in.get("/api/me").status_code == 200
    signed_in.post("/api/auth/sign-out")
    r = signed_in.post(
        "/api/auth/sign-in", json={"username": "admin", "password": "battery staple"}
    )
    assert r.status_code == 204


def test_change_password_needs_the_current_one(signed_in):
    r = signed_in.post(
        "/api/me/password", json={"current_password": "wrong", "new_password": "battery staple"}
    )
    assert r.status_code == 400
    assert r.json() == {"detail": "Your current password doesn't match."}


def test_change_password_rejects_a_short_new_one(signed_in):
    r = signed_in.post(
        "/api/me/password", json={"current_password": "correct horse", "new_password": "short"}
    )
    assert r.status_code == 422


def test_change_password_signs_out_other_sessions(signed_in, settings):
    with TestClient(create_app(settings)) as other:
        assert other.post("/api/auth/sign-in", json=OK).status_code == 204
        assert other.get("/api/me").status_code == 200
        signed_in.post(
            "/api/me/password",
            json={"current_password": "correct horse", "new_password": "battery staple"},
        )
        assert other.get("/api/me").status_code == 401
    assert signed_in.get("/api/me").status_code == 200


def test_profile_routes_require_sign_in(client):
    assert client.patch("/api/me", json={}).status_code == 401
    body = {"current_password": "a", "new_password": "b" * 10}
    assert client.post("/api/me/password", json=body).status_code == 401
