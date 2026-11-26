import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vegapunk.app import create_app
from vegapunk.config import Settings
from vegapunk.db import connect, migrate, prepare_data_dir


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", static_dir=None, secure_cookies=False)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def conn(settings: Settings, client: TestClient) -> Iterator[sqlite3.Connection]:
    c = connect(settings.db_path)
    migrate(c)
    yield c
    c.close()


CREDENTIALS = {"username": "admin", "password": "correct horse"}


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    assert client.post("/api/setup/admin", json=CREDENTIALS).status_code == 201
    return client


@pytest.fixture
def admin_client(signed_in: TestClient) -> TestClient:
    signed_in.cookies.clear()
    return signed_in


@pytest.fixture
def db_path(settings: Settings) -> Path:
    """A created, migrated database, with no app running against it."""
    prepare_data_dir(settings)
    c = connect(settings.db_path)
    migrate(c)
    c.close()
    return settings.db_path
