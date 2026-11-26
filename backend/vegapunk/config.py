import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    static_dir: Path | None
    secure_cookies: bool
    capture: bool = False
    capture_interval: int = 15
    docker_url: str = "unix:///var/run/docker.sock"
    disk_path: Path = Path("/")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "vegapunk.db"

    @property
    def conversations_path(self) -> Path:
        """The agent's conversations, kept by logpose in its own database file."""
        return self.data_dir / "conversations.db"


def _interval(env: Mapping[str, str]) -> int:
    raw = env.get("VEGAPUNK_CAPTURE_INTERVAL", "15").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise ValueError(
            f"VEGAPUNK_CAPTURE_INTERVAL must be a whole number of seconds, 1 or more (got {raw!r})"
        )
    return value


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    static = env.get("VEGAPUNK_STATIC_DIR", "").strip()
    return Settings(
        data_dir=Path(env.get("VEGAPUNK_DATA_DIR", "data")),
        static_dir=Path(static) if static else None,
        secure_cookies=env.get("VEGAPUNK_SECURE_COOKIES") == "1",
        capture=True,
        capture_interval=_interval(env),
        docker_url=env.get("VEGAPUNK_DOCKER_URL", "unix:///var/run/docker.sock"),
        disk_path=Path(env.get("VEGAPUNK_DISK_PATH", "/")),
    )
