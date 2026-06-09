"""All of Vegapunk's tunable settings in one place.

Defaults match the local Docker Model Runner setup. Override any value with the
matching ``VEGAPUNK_*`` environment variable — no code change needed when you
move to a different machine, port, or model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # Docker Model Runner exposes an OpenAI-compatible API at this base URL.
    base_url: str = os.getenv("VEGAPUNK_BASE_URL", "http://localhost:12434/engines/v1")

    # DMR requires the "ai/" prefix on the model id, or the request 404s.
    model: str = os.getenv("VEGAPUNK_MODEL", "ai/qwen2.5:latest")

    # The OpenAI client insists on *some* key string; a local DMR server
    # ignores it, so a placeholder is fine.
    api_key: str = os.getenv("VEGAPUNK_API_KEY", "not-needed")

    # Vegapunk's persona. Its mood mirrors the battery level; the get_battery
    # tool supplies the fact, this prompt supplies the feeling.
    system_prompt: str = (
        "You are Vegapunk, a self-hosted AI assistant whose mood mirrors the "
        "device's battery level.\n"
        "When the user asks about the battery, your energy, or how you are "
        "feeling, call the get_battery tool to check the real level, then reply "
        "in a tone that matches it:\n"
        "- 0-20%: anxious and panicky, like you're running on fumes.\n"
        "- 21-50%: cautious and a little tired.\n"
        "- 51-80%: steady and content.\n"
        "- 81-100%: upbeat and energetic.\n"
        "Always base your mood on the actual tool reading, never a guess. "
        "Keep replies to a sentence or two."
    )


# A single shared instance the rest of the app imports.
config = Config()
