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
    # it stops. Higher gives the model room for real multi-step tasks and to
    # recover from failed steps; the limit exists as the runaway-loop backstop,
    # not as a working budget.
    max_steps: int = int(os.getenv("VEGAPUNK_MAX_STEPS", "25"))

    # Color in the CLI: "auto" (color only when the stream is a terminal),
    # "always" (even when piped — for `| less -R`), or "never". The NO_COLOR
    # cross-tool standard (https://no-color.org) also disables it.
    color: str = os.getenv("VEGAPUNK_COLOR", "auto")

    # The model's context window (tokens), for the toolbar's fullness gauge.
    # DMR doesn't expose it over the API, so it's declared here; the default
    # matches the local DMR setup (check yours: `docker model logs | grep
    # n_ctx`). Set 0 if unknown — the gauge then shows tokens without a %.
    context_window: int = int(os.getenv("VEGAPUNK_CONTEXT_WINDOW", "131072"))

    # Which brain to start with: "local" (the DMR model above) or "claude"
    # (subscription-billed Claude via the bundled Claude Code CLI). Switch live
    # with /model; this only sets the default at launch.
    provider: str = os.getenv("VEGAPUNK_PROVIDER", "local")

    # Claude model override (e.g. "sonnet", "opus", or a full model id).
    # Empty means whatever the Claude Code account is configured to use.
    claude_model: str = os.getenv("VEGAPUNK_CLAUDE_MODEL", "")

    # Claude's context window (tokens), for the toolbar gauge — same role as
    # context_window above but for the claude provider.
    claude_context_window: int = int(os.getenv("VEGAPUNK_CLAUDE_CONTEXT_WINDOW", "200000"))

    # Claude's effort level: low|medium|high|xhigh|max. Empty means the SDK
    # default ("high"). Adjust live with /effort. Validated by ClaudeBrain,
    # not here — config must not import the SDK (local-only setups never do).
    claude_effort: str = os.getenv("VEGAPUNK_CLAUDE_EFFORT", "")

    # The embedded database holding sessions, long-term memory, and REPL input
    # history. Defaults to vegapunk.db at the project root (the launch
    # directory). One Vegapunk process at a time (enforced with a lock file);
    # snapshot with /backup. Contents are readable with any sqlite3 client, so
    # the no-secrets posture still applies.
    db_file: Path = Path(
        os.getenv("VEGAPUNK_DB_FILE", str(Path.cwd() / "vegapunk.db"))
    ).expanduser()

    # Embedding model for semantic memory recall, served by Docker Model Runner's
    # OpenAI-compatible /embeddings endpoint (e.g. "ai/qwen3-embedding" after
    # `docker model pull ai/qwen3-embedding`). Empty disables embeddings — memory
    # still works; the recall tool falls back to plain text matching.
    embed_model: str = os.getenv("VEGAPUNK_EMBED_MODEL", "")

    # Where skills live — reusable procedures in the Agent Skills format
    # (https://agentskills.io): one directory per skill holding a SKILL.md,
    # each advertised to the model as a one-line description at startup and
    # pulled in on demand via the use_skill tool. The tool-agnostic .agents/
    # location means skills written for other agents drop in unchanged.
    skills_dir: Path = Path(
        os.getenv("VEGAPUNK_SKILLS_DIR", str(Path.cwd() / ".agents" / "skills"))
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
