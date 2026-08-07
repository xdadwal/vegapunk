"""Entry point so ``python -m vegapunk`` launches the interactive REPL."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from vegapunk.cli import main


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


if __name__ == "__main__":
    main(auto=_parse_args().auto)
