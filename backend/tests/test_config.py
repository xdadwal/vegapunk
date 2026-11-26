from pathlib import Path

from vegapunk.config import load_settings


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
