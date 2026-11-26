"""Encrypting the AI provider's API key at rest.

The key lives in `secret.key` in the data directory. That keeps the API key out of a copied
database or backup. It does not protect against someone who can read the whole data folder:
the key and the ciphertext sit side by side.
"""

import contextlib
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

KEY_FILE = "secret.key"


class CryptoError(Exception):
    """The message is safe to show people."""


def _create(path: Path) -> None:
    """Write a new key so that `path` never exists half-written.

    The key goes to a temporary file first and is hard-linked into place, so a crash
    cannot leave an empty key file that would block startup for good. If another process
    created the file first, theirs is used.
    """
    tmp = path.with_name(f".{KEY_FILE}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        try:
            os.write(fd, Fernet.generate_key())
        finally:
            os.close(fd)
        try:
            os.link(tmp, path)
        except FileExistsError:
            pass
        except OSError:
            # A filesystem without hard links. A rename is still atomic.
            if not path.exists():
                os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


def load_or_create_key(data_dir: Path) -> bytes:
    path = data_dir / KEY_FILE
    if not path.exists():
        _create(path)
    if path.stat().st_mode & 0o077:
        path.chmod(0o600)
    key = path.read_bytes().strip()
    try:
        Fernet(key)
    except (ValueError, TypeError):
        # Never regenerate: that would silently orphan a saved API key.
        raise CryptoError(
            f"The key file {path} is not valid, so a saved API key can't be read. "
            "Restore it from a backup, or delete it and enter the API key again."
        ) from None
    return key


def encrypt(key: bytes, text: str) -> str:
    return Fernet(key).encrypt(text.encode()).decode()


def decrypt(key: bytes, token: str) -> str:
    try:
        return Fernet(key).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        raise CryptoError("The saved API key can't be read. Enter it again.") from None


def key_hint(api_key: str) -> str:
    """The last four characters, and only for a key long enough that this is not most of it."""
    return f"…{api_key[-4:]}" if len(api_key) >= 12 else "…"
