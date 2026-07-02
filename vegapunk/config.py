"""All of Vegapunk's tunable settings in one place.

Defaults match the local Docker Model Runner setup. Override any value with the
matching ``VEGAPUNK_*`` environment variable — no code change needed when you
move to a different machine, port, or model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    # Docker Model Runner exposes an OpenAI-compatible API at this base URL.
    base_url: str = os.getenv("VEGAPUNK_BASE_URL", "http://localhost:12434/engines/v1")

    # DMR needs a fully-qualified model id (e.g. docker.io/gemma4:latest or
    # ai/qwen2.5:latest), or the request 404s.
    model: str = os.getenv("VEGAPUNK_MODEL", "docker.io/gemma4:latest")

    # The OpenAI client insists on *some* key string; a local DMR server
    # ignores it, so a placeholder is fine.
    api_key: str = os.getenv("VEGAPUNK_API_KEY", "not-needed")

    # The root the filesystem and shell tools are confined to. Defaults to the
    # directory Vegapunk was launched in, so it operates on the project you're
    # in; point it elsewhere to sandbox it. Reads/writes outside it are refused.
    workspace_root: str = os.getenv("VEGAPUNK_WORKSPACE", os.getcwd())

    # How long a shell command may run before it's killed (seconds).
    shell_timeout: float = float(os.getenv("VEGAPUNK_SHELL_TIMEOUT", "30"))

    # Cap on tool output (chars) fed back to the model, to protect the context
    # window; anything longer is truncated with a visible marker.
    output_char_cap: int = int(os.getenv("VEGAPUNK_OUTPUT_CAP", "10000"))

    # How many think->act->observe steps the agent may take in one turn before
    # it stops. Higher gives the model room to recover from a failed step and
    # try another approach; lower reins in a runaway loop.
    max_steps: int = int(os.getenv("VEGAPUNK_MAX_STEPS", "8"))

    # Color in the CLI: "auto" (color only when the stream is a terminal),
    # "always" (even when piped — for `| less -R`), or "never". The NO_COLOR
    # cross-tool standard (https://no-color.org) also disables it.
    color: str = os.getenv("VEGAPUNK_COLOR", "auto")

    # The REPL input history file (up/down recall, persisted across sessions).
    # Defaults under the current directory's root so state stays with the
    # project you launched in; override with VEGAPUNK_HISTORY_FILE. Stored in
    # plaintext, so avoid pasting secrets into the prompt.
    history_file: Path = Path(
        os.getenv("VEGAPUNK_HISTORY_FILE", str(Path.cwd() / ".vegapunk" / "history"))
    ).expanduser()

    # Vegapunk's long-term memory: durable facts/preferences it should still know
    # next session. Auto-loaded into the system prompt at startup and appended to
    # by the `remember` tool. Plaintext and human-editable, so avoid saving
    # secrets. Defaults under the current directory, like history_file.
    memory_file: Path = Path(
        os.getenv("VEGAPUNK_MEMORY_FILE", str(Path.cwd() / ".vegapunk" / "memory.md"))
    ).expanduser()

    # Where saved conversations live (one JSON file per session). Conversations
    # auto-save here each turn under a model-chosen name. Plaintext and
    # human-editable, so — like memory — avoid pasting secrets into a chat.
    sessions_dir: Path = Path(
        os.getenv("VEGAPUNK_SESSIONS_DIR", str(Path.cwd() / ".vegapunk" / "sessions"))
    ).expanduser()

    # Vegapunk's identity + how it operates. The "How you work" stanza keeps a
    # small model self-correcting after a failed step instead of apologizing
    # and giving up.
    system_prompt: str = (
        "You are Vegapunk, a self-hosted AI assistant that gets things done "
        "with tools in the user's workspace.\n"
        "\n"
        "How you work:\n"
        "- Get the request done by using tools, reading each result, and "
        "continuing — don't stop at the first obstacle.\n"
        "- Treat a tool result that's an error or guidance as a correction, not "
        "a dead end: fix the arguments, or try a different tool or approach, and "
        "continue. Don't apologize and give up after one failed try.\n"
        "- Never claim something is done unless a tool result shows it; don't "
        "pretend an action succeeded.\n"
        "- If the user denies a tool, or the same step keeps failing the same "
        "way, don't repeat it — switch approaches, or tell the user what's "
        "blocking you and what you need.\n"
        "- When the request is genuinely ambiguous, or needs a detail only the "
        "user can give (which file, which of several options, a preference), ask "
        "one short clarifying question and wait for their answer instead of "
        "guessing. This is for missing information only — keep working through "
        "tool errors and obstacles yourself.\n"
        "- When the user states a durable fact or preference about themselves "
        "(their tools, environment, how they like things done), or asks you to "
        "remember something, call remember to save it for future sessions. Don't "
        "save ephemeral, one-off task details.\n"
        "- Stop only when the task is genuinely done, or you've tried the "
        "reasonable options and are truly stuck — then briefly say what you tried.\n"
        "\n"
        "You can read files, write files, and run shell commands in your "
        "workspace; writing files and running commands need the user's approval, "
        "so use them when a task needs real action and say what you intend to do.\n"
        "\n"
        "Keep your final reply to the user brief — a sentence or two. Taking "
        "several tool steps to get there is fine; brevity is about the answer, "
        "not the effort."
    )


# A single shared instance the rest of the app imports.
config = Config()
