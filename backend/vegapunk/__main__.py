import argparse
import getpass
import sys
from collections.abc import Callable
from importlib import metadata

from vegapunk.auth import admin as admin_store
from vegapunk.auth import sessions
from vegapunk.config import load_settings
from vegapunk.db import connect, transaction


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def reset_password(read_password: Callable[[str], str]) -> int:
    settings = load_settings()
    if not settings.db_path.exists():
        return _fail(f"No Vegapunk database found in {settings.data_dir}. Start Vegapunk first.")
    conn = connect(settings.db_path)
    try:
        if admin_store.get_admin(conn) is None:
            return _fail("There is no admin account yet. Open Vegapunk in a browser to create one.")
        password = read_password("New password: ")
        if password != read_password("Confirm new password: "):
            return _fail("The two passwords don't match.")
        if len(password) < admin_store.MIN_PASSWORD_LENGTH:
            return _fail(
                f"The password must be at least {admin_store.MIN_PASSWORD_LENGTH} characters."
            )
        with transaction(conn):
            admin_store.set_password(conn, password)
            sessions.delete_all_sessions(conn)
            sessions.clear_failures(conn)
    finally:
        conn.close()
    print("Password reset. Everyone was signed out.")
    return 0


def show_version() -> int:
    try:
        version = metadata.version("vegapunk")
    except metadata.PackageNotFoundError:
        return _fail("Vegapunk is not installed as a package, so its version is unknown.")
    print(f"Vegapunk {version}")
    return 0


def main(
    argv: list[str] | None = None,
    *,
    read_password: Callable[[str], str] = getpass.getpass,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m vegapunk")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("reset-password", help="set a new admin password from the host")
    commands.add_parser("version", help="print the Vegapunk version")
    args = parser.parse_args(argv)
    if args.command == "version":
        return show_version()
    return reset_password(read_password)


if __name__ == "__main__":
    raise SystemExit(main())
