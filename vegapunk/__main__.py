"""Entry point so ``python -m vegapunk`` launches the interactive REPL."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m vegapunk",
        description="Run the Vegapunk personal agent.",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="allow guarded tools without approval for this session",
    )
    return parser.parse_args(argv)


def _main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    # Configuration defaults are bound at import time. Load only the explicit
    # workspace file first; never search parent directories or override exports.
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    from vegapunk.cli import main

    main(auto=args.auto)


if __name__ == "__main__":
    _main()
