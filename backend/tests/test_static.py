from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vegapunk.app import create_app
from vegapunk.config import Settings


@pytest.fixture
def static_client(tmp_path: Path):
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>Vegapunk</title>")
    (static / "assets" / "app.js").write_text("console.log('hi')")
    settings = Settings(data_dir=tmp_path / "data", static_dir=static, secure_cookies=False)
    with TestClient(create_app(settings)) as c:
        yield c


def test_serves_index_and_assets(static_client):
    assert "Vegapunk" in static_client.get("/").text
    assert static_client.get("/assets/app.js").status_code == 200


def test_client_side_routes_fall_back_to_index(static_client):
    r = static_client.get("/profile")
    assert r.status_code == 200
    assert "Vegapunk" in r.text


def test_missing_asset_is_a_404_not_the_index(static_client):
    assert static_client.get("/assets/missing.js").status_code == 404


def test_unknown_api_path_stays_a_json_404(static_client):
    r = static_client.get("/api/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_api_still_works_alongside_static(static_client):
    assert static_client.get("/api/health").json() == {"status": "ok"}


def test_cannot_read_files_outside_the_static_dir(static_client, tmp_path):
    (tmp_path / "secret.txt").write_text("secret")
    r = static_client.get("/..%2Fsecret.txt")
    assert "secret" not in r.text


def test_no_static_dir_means_no_fallback(client):
    assert client.get("/profile").status_code == 404
