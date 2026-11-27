import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    static_dir: Path | None
    secure_cookies: bool

    @property
    def db_path(self) -> Path:
        return self.data_dir / "vegapunk.db"


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    static = env.get("VEGAPUNK_STATIC_DIR", "").strip()
    return Settings(
        data_dir=Path(env.get("VEGAPUNK_DATA_DIR", "data")),
        static_dir=Path(static) if static else None,
        secure_cookies=env.get("VEGAPUNK_SECURE_COOKIES") == "1",
    )
