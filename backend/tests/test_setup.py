import pytest

from vegapunk.auth.passwords import hash_password, verify_password


def test_status_before_and_after_setup(client):
    assert client.get("/api/setup/status").json() == {"admin_created": False}
    r = client.post("/api/setup/admin", json={"username": "admin", "password": "correct horse"})
    assert r.status_code == 201
    assert r.json()["username"] == "admin"
    assert r.json()["display_name"] == "admin"
    assert client.get("/api/setup/status").json() == {"admin_created": True}


def test_second_setup_is_rejected_and_changes_nothing(client, conn):
    client.post("/api/setup/admin", json={"username": "admin", "password": "correct horse"})
    r = client.post("/api/setup/admin", json={"username": "other", "password": "another pass"})
    assert r.status_code == 409
    assert r.json() == {"detail": "The admin account already exists."}
    assert [row["username"] for row in conn.execute("SELECT username FROM admin")] == ["admin"]


@pytest.mark.parametrize(
    "body",
    [
        {"username": "Admin", "password": "correct horse"},
        {"username": "", "password": "correct horse"},
        {"username": "a" * 33, "password": "correct horse"},
        {"username": "admin", "password": "short"},
    ],
)
def test_invalid_input_is_rejected(client, body):
    assert client.post("/api/setup/admin", json=body).status_code == 422
    assert client.get("/api/setup/status").json() == {"admin_created": False}


def test_password_is_stored_hashed(client, conn):
    client.post("/api/setup/admin", json={"username": "admin", "password": "correct horse"})
    stored = conn.execute("SELECT password_hash FROM admin").fetchone()[0]
    assert "correct horse" not in stored
    assert verify_password("correct horse", stored)


def test_verify_rejects_wrong_and_malformed():
    assert not verify_password("x", hash_password("y"))
    assert not verify_password("x", "not-a-hash")
