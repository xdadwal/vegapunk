from pathlib import Path

import pytest

from vegapunk.config import Settings, load_settings


def test_defaults():
    s = load_settings({})
    assert s.data_dir == Path("data")
    assert s.static_dir is None
    assert s.secure_cookies is False
    assert s.db_path == Path("data/vegapunk.db")


def test_reads_environment():
    s = load_settings(
        {
            "VEGAPUNK_DATA_DIR": "/d",
            "VEGAPUNK_STATIC_DIR": "/s",
            "VEGAPUNK_SECURE_COOKIES": "1",
        }
    )
    assert (s.data_dir, s.static_dir, s.secure_cookies) == (Path("/d"), Path("/s"), True)


def test_blank_static_dir_is_none():
    assert load_settings({"VEGAPUNK_STATIC_DIR": " "}).static_dir is None


def test_capture_settings_defaults_and_env():
    s = load_settings({})
    assert (s.capture, s.capture_interval, s.docker_url, s.disk_path) == (
        True,
        15,
        "unix:///var/run/docker.sock",
        Path("/"),
    )
    s = load_settings({"VEGAPUNK_CAPTURE_INTERVAL": "30", "VEGAPUNK_DISK_PATH": "/host/data"})
    assert (s.capture_interval, s.disk_path) == (30, Path("/host/data"))


@pytest.mark.parametrize("value", ["abc", "0", "-1", "1.5"])
def test_bad_interval_names_the_variable(value):
    with pytest.raises(ValueError, match="VEGAPUNK_CAPTURE_INTERVAL"):
        load_settings({"VEGAPUNK_CAPTURE_INTERVAL": value})


def test_existing_settings_construction_still_works():
    s = Settings(data_dir=Path("d"), static_dir=None, secure_cookies=False)
    assert (s.capture, s.capture_interval) == (False, 15)


def test_conversations_live_beside_the_database():
    s = Settings(data_dir=Path("d"), static_dir=None, secure_cookies=False)
    assert s.conversations_path == Path("d/conversations.db")
    assert s.db_path == Path("d/vegapunk.db")
