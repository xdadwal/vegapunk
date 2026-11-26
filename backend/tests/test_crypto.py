import errno
import os
import stat
import threading

import pytest
from cryptography.fernet import Fernet

from vegapunk.crypto import CryptoError, decrypt, encrypt, key_hint, load_or_create_key


def test_round_trip():
    key = Fernet.generate_key()
    token = encrypt(key, "sk-secret-value")
    assert "sk-secret-value" not in token
    assert decrypt(key, token) == "sk-secret-value"


def test_key_file_is_created_with_mode_0600(tmp_path):
    load_or_create_key(tmp_path)
    assert stat.S_IMODE((tmp_path / "secret.key").stat().st_mode) == 0o600


def test_existing_key_is_reused(tmp_path):
    assert load_or_create_key(tmp_path) == load_or_create_key(tmp_path)


def test_loose_permissions_are_tightened(tmp_path):
    key = load_or_create_key(tmp_path)
    (tmp_path / "secret.key").chmod(0o644)
    assert load_or_create_key(tmp_path) == key
    assert stat.S_IMODE((tmp_path / "secret.key").stat().st_mode) == 0o600


def test_invalid_key_file_is_an_error_and_is_not_replaced(tmp_path):
    path = tmp_path / "secret.key"
    path.write_text("not a fernet key")
    with pytest.raises(CryptoError, match=str(path)):
        load_or_create_key(tmp_path)
    assert path.read_text() == "not a fernet key"


def test_an_empty_key_file_is_an_error_and_is_not_replaced(tmp_path):
    path = tmp_path / "secret.key"
    path.write_bytes(b"")
    with pytest.raises(CryptoError, match=str(path)):
        load_or_create_key(tmp_path)
    assert path.read_bytes() == b""


def test_a_crash_while_writing_cannot_leave_an_empty_key_file(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr("vegapunk.crypto.os.write", boom)
    with pytest.raises(OSError):
        load_or_create_key(tmp_path)
    assert list(tmp_path.iterdir()) == []  # no secret.key, no leftover temporary file
    monkeypatch.undo()
    assert load_or_create_key(tmp_path)  # and a retry works


def test_a_filesystem_without_hard_links_still_works(tmp_path, monkeypatch):
    def no_links(*args, **kwargs):
        raise OSError(errno.EPERM, "links not supported")

    monkeypatch.setattr("vegapunk.crypto.os.link", no_links)
    key = load_or_create_key(tmp_path)
    assert load_or_create_key(tmp_path) == key
    assert [p.name for p in tmp_path.iterdir()] == ["secret.key"]


def test_simultaneous_first_use_agrees_on_one_key(tmp_path):
    keys, errors = [], []

    def go():
        try:
            keys.append(load_or_create_key(tmp_path))
        except Exception as exc:  # pragma: no cover - the assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(set(keys)) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["secret.key"]


def test_wrong_key_cannot_decrypt():
    token = encrypt(Fernet.generate_key(), "x")
    with pytest.raises(CryptoError, match="Enter it again"):
        decrypt(Fernet.generate_key(), token)


def test_garbage_token_cannot_decrypt():
    with pytest.raises(CryptoError):
        decrypt(Fernet.generate_key(), "not-a-token")


@pytest.mark.parametrize(
    "key,hint", [("sk-abcdefghijkl4f2a", "…4f2a"), ("sk-abcdefgh", "…"), ("short", "…"), ("", "…")]
)
def test_key_hint_never_reveals_a_short_key(key, hint):
    assert key_hint(key) == hint


def test_the_key_file_is_not_world_readable_even_when_created_under_a_loose_umask(tmp_path):
    old = os.umask(0)
    try:
        load_or_create_key(tmp_path)
    finally:
        os.umask(old)
    assert stat.S_IMODE((tmp_path / "secret.key").stat().st_mode) == 0o600


def test_the_key_is_flushed_to_disk_before_it_is_linked_into_place(tmp_path, monkeypatch):
    import os

    from vegapunk import crypto

    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    crypto.load_or_create_key(tmp_path)
    assert synced
