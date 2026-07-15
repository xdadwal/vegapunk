"""The REPL's slash commands — ``/help``, ``/save``, ``/load``, ``/sessions``,
``/new``, ``/exit``.

Mirrors the ``@tool`` registry (``tools/registry.py``): a ``@command`` decorator
registers a handler into ``REGISTRY``, so adding a command is one function and
``/help`` is generated from the registry. The CLI calls ``dispatch`` on each
``/``-prefixed line; non-command input is sent to the model instead.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable

from . import db, memory, session_store, skills
from .brain import create_brain
from .config import config
from .session import Session

# A handler takes the live context and the text after the command name.
Handler = Callable[["CommandContext", str], "CommandResult"]


@dataclass
class CommandContext:
    """Mutable REPL state handed to every command; handlers may reassign fields."""

    session: Session
    current_name: str | None = None
    # A skill staged by /skill, to be folded into the user's NEXT message
    # (name, body) — the CLI consumes and clears it; /new drops it.
    pending_skill: tuple[str, str] | None = None


@dataclass
class CommandResult:
    output: str = ""  # what the REPL prints
    exit: bool = False  # signal the REPL to quit


@dataclass
class Command:
    name: str
    summary: str
    handler: Handler


# name and aliases both map to the same Command.
REGISTRY: dict[str, Command] = {}


def command(name: str, summary: str, *aliases: str) -> Callable[[Handler], Handler]:
    """Register a slash-command handler. Like ``@tool``, but for the REPL."""

    def decorate(fn: Handler) -> Handler:
        cmd = Command(name=name, summary=summary, handler=fn)
        for key in (name, *aliases):
            REGISTRY[key] = cmd
        return fn

    return decorate


def dispatch(line: str, ctx: CommandContext) -> CommandResult | None:
    """Run a ``/cmd args`` line. Returns ``None`` when ``line`` isn't a slash
    command, so the caller knows to send it to the model instead."""
    if not line.startswith("/"):
        return None
    name, _, arg = line[1:].strip().partition(" ")
    cmd = REGISTRY.get(name.lower())
    if cmd is None:
        return CommandResult(output=f"Unknown command /{name}. Type /help for the list.")
    return cmd.handler(ctx, arg.strip())


def _local_stamp(iso_utc: str) -> str:
    """Render a stored UTC timestamp (db.utcnow format) as local-time
    ``YYYY-MM-DD HH:MM`` — the minute lets same-day sessions be told apart.
    Falls back to the raw date+time on an unparseable value."""
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except ValueError:
        return iso_utc[:16].replace("T", " ")
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def _format_sessions() -> str:
    rows = session_store.list_sessions(limit=5)
    if not rows:
        return "(no saved sessions)"
    return "\n".join(
        f"  {name}  ({turns} turns, {_local_stamp(updated_at)})" for name, turns, updated_at in rows
    )


@command("help", "Show this help")
def _help(ctx: CommandContext, arg: str) -> CommandResult:
    seen: set[str] = set()
    lines = []
    for cmd in REGISTRY.values():
        if cmd.name in seen:
            continue
        seen.add(cmd.name)
        lines.append(f"  /{cmd.name:<9} {cmd.summary}")
    return CommandResult(output="Commands:\n" + "\n".join(lines))


@command("exit", "Quit Vegapunk", "quit")
def _exit(ctx: CommandContext, arg: str) -> CommandResult:
    return CommandResult(output="bye.", exit=True)


@command("new", "Start a fresh conversation", "reset", "clear")
def _new(ctx: CommandContext, arg: str) -> CommandResult:
    ctx.session.reset()
    ctx.current_name = None  # next turn auto-names a fresh saved session
    ctx.pending_skill = None  # a fresh conversation drops staged state too
    return CommandResult(output="(new conversation)")


@command("model", "Show or switch the model: /model [local|claude [model]]")
def _model(ctx: CommandContext, arg: str) -> CommandResult:
    if not arg:
        return CommandResult(
            output=f"Active: {ctx.session.brain.model_label}\n"
            "Available: local (Docker Model Runner), claude [model] (Claude subscription)"
        )
    tokens = arg.split()
    provider = tokens[0].lower()
    model = tokens[1] if len(tokens) == 2 else ""
    # Provider is validated here, not via create_brain's ValueError — that
    # channel must stay free for real construction errors (e.g. a junk
    # VEGAPUNK_CLAUDE_EFFORT), which deserve their own message, not "Usage:".
    if len(tokens) > 2 or provider not in ("local", "claude") or (model and provider != "claude"):
        return CommandResult(output="Usage: /model [local|claude [model]]")
    cfg = replace(config, claude_model=model) if model else config
    try:
        brain = create_brain(provider, cfg)
    except ValueError as exc:
        return CommandResult(output=str(exc))
    # Carry a /effort choice across claude→claude swaps (a claude→local→claude
    # round trip loses it — the local brain has nowhere to hold it).
    effort = getattr(ctx.session.brain, "effort", None)
    if effort and hasattr(brain, "set_effort"):
        brain.set_effort(effort)
    ctx.session.swap_brain(brain)
    return CommandResult(
        output=f"(model switched to {brain.model_label} — the conversation continues)"
    )


@command("effort", "Show or set Claude's effort: /effort [low|medium|high|xhigh|max]")
def _effort(ctx: CommandContext, arg: str) -> CommandResult:
    brain = ctx.session.brain
    # Duck-typed on set_effort — commands.py never imports ClaudeBrain or the
    # SDK (local-only setups don't pay that import). hasattr, not getattr on
    # `effort`: a Claude brain at the SDK default legitimately has effort=None.
    if not hasattr(brain, "set_effort"):
        return CommandResult(
            output="(the local model has no effort setting — /model claude first)"
        )
    if not arg:
        # None = unset; the SDK's documented default is "high".
        current = brain.effort or "high (default)"
        return CommandResult(output=f"Effort: {current}")
    try:
        brain.set_effort(arg.lower())
    except ValueError as exc:
        return CommandResult(output=str(exc))  # names the valid levels
    return CommandResult(output=f"(effort set to {arg.lower()})")


@command("save", "Rename the current session: /save <name>")
def _save(ctx: CommandContext, arg: str) -> CommandResult:
    name = session_store.slugify(arg)
    if not name:
        return CommandResult(output="Usage: /save <name>")
    try:
        if name != ctx.current_name and session_store.exists(name):
            return CommandResult(
                output=f"A session named '{name}' already exists — choose another name."
            )
        session_store.save_session(name, ctx.session.messages)
        if ctx.current_name and ctx.current_name != name:
            session_store.delete_session(ctx.current_name)  # rename: drop the old (auto-named) row
    except db.StoreError as exc:
        return CommandResult(output=f"Could not save: {exc}")
    ctx.current_name = name
    return CommandResult(output=f"Saved as '{name}'.")


@command("load", "Resume a saved session: /load <name>")
def _load(ctx: CommandContext, arg: str) -> CommandResult:
    name = session_store.slugify(arg)
    if not name:
        return CommandResult(output="Usage: /load <name>")
    try:
        messages = session_store.load_session(name)
    except session_store.SessionNotFound:
        return CommandResult(output=f"No session '{name}'.\n{_format_sessions()}")
    except db.StoreError as exc:
        return CommandResult(output=f"Could not load '{name}': {exc}")
    ctx.session.restore(messages)
    ctx.current_name = name
    ctx.pending_skill = None  # staged state belongs to the conversation it was staged in
    turns = sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "user")
    return CommandResult(output=f"Resumed '{name}' ({turns} turns).")


@command("sessions", "List recent conversations, or delete one: /sessions [forget <name>]")
def _sessions(ctx: CommandContext, arg: str) -> CommandResult:
    sub, _, rest = arg.partition(" ")
    sub = sub.strip().lower()
    if not sub:
        return CommandResult(output=_format_sessions())
    if sub == "forget":
        name = session_store.slugify(rest)
        if not name:
            return CommandResult(output="Usage: /sessions forget <name>")
        try:
            if not session_store.exists(name):
                return CommandResult(output=f"No session '{name}' to forget.\n{_format_sessions()}")
            session_store.delete_session(name)
        except db.StoreError as exc:
            return CommandResult(output=f"Could not forget '{name}': {exc}")
        if ctx.current_name == name:
            # The live conversation's saved copy is gone; the next turn re-saves
            # it under a fresh name rather than resurrecting the deleted one.
            ctx.current_name = None
        return CommandResult(output=f"Forgot session '{name}'.")
    return CommandResult(output="Usage: /sessions [forget <name>]")


@command("memory", "List or forget remembered facts: /memory [list | forget <id>]")
def _memory(ctx: CommandContext, arg: str) -> CommandResult:
    sub, _, rest = arg.partition(" ")
    sub = sub.strip().lower()
    if sub in ("", "list"):
        rows = memory.list_memory()
        if not rows:
            return CommandResult(output="(nothing remembered yet)")
        lines = [f"  {m.id[:8]}  {m.created_at[:10]}  {_oneline(m.content)}" for m in rows]
        return CommandResult(output="\n".join(lines))
    if sub == "forget":
        result = memory.forget_memory(rest)
        if result.startswith("Forgot:"):
            result += " (the system prompt updates next session)"
        return CommandResult(output=result)
    return CommandResult(output="Usage: /memory [list | forget <id>]")


@command("backup", "Snapshot the database: /backup")
def _backup(ctx: CommandContext, arg: str) -> CommandResult:
    try:
        path = db.backup_now()
    except db.StoreError as exc:
        return CommandResult(output=f"Backup failed: {exc}")
    return CommandResult(output=f"Backed up to {path}")


@command("skills", "List available skills (SKILL.md directories under .agents/skills/)")
def _skills(ctx: CommandContext, arg: str) -> CommandResult:
    rows = skills.list_skills()
    if not rows:
        return CommandResult(
            output=f"(no skills — add <name>/SKILL.md directories under {skills.skills_dir()})"
        )
    return CommandResult(output="\n".join(f"  {s.name} — {s.description}" for s in rows))


@command("skill", "Stage a skill for your next message: /skill <name>")
def _skill(ctx: CommandContext, arg: str) -> CommandResult:
    """Force a skill by hand — for when the model doesn't reach for it. The
    body rides the next message instead of a use_skill round-trip."""
    if not arg:
        names = ", ".join(s.name for s in skills.list_skills()) or "(none installed)"
        return CommandResult(output=f"Usage: /skill <name>. Available: {names}")
    try:
        skill, body = skills.load_skill(arg)
    except skills.SkillNotFound as exc:
        names = ", ".join(exc.available) or "(none installed)"  # no second discovery pass
        return CommandResult(output=f"No skill matches '{arg}'. Available: {names}")
    if len(body) > config.output_char_cap:  # same cap as the use_skill tool
        body = body[: config.output_char_cap] + "\n...[truncated]"
    note = skills.file_reference_note(skill)
    if note:  # after the cap: the pointer to bundled files must never be truncated away
        body = f"{body}\n\n{note}"
    ctx.pending_skill = (skill.name, body)
    return CommandResult(output=f"(skill '{skill.name}' will be included with your next message)")


def _oneline(text: str | None, cap: int = 200) -> str:
    """Collapse a message to a single, length-capped line for the history view."""
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= cap else collapsed[: cap - 1] + "…"


def _recent_turns(messages: list[dict], limit: int) -> list[tuple[str, str | None]]:
    """The last ``limit`` exchanges as ``(user_text, reply_text)`` pairs.

    Pairs each user message with the assistant's next text reply; the system turn,
    tool turns, and tool-call-only assistant turns (no content) are skipped. A
    trailing user message with no reply yet pairs with ``None``.
    """
    turns: list[tuple[str, str | None]] = []
    pending: str | None = None
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if role == "user":
            if pending is not None:
                turns.append((pending, None))
            pending = content
        elif role == "assistant" and content and pending is not None:
            turns.append((pending, content))
            pending = None
    if pending is not None:
        turns.append((pending, None))
    return turns[-limit:]


@command("history", "Show the last few turns of this conversation: /history [n]")
def _history(ctx: CommandContext, arg: str) -> CommandResult:
    limit = 5
    if arg:
        try:
            limit = max(1, int(arg))
        except ValueError:
            return CommandResult(output="Usage: /history [n]  (n = number of turns, default 5)")
    turns = _recent_turns(ctx.session.messages, limit)
    if not turns:
        return CommandResult(output="(no conversation yet)")
    lines = []
    for user_text, reply_text in turns:
        lines.append(f"  you:  {_oneline(user_text)}")
        lines.append(f"  vega: {_oneline(reply_text) if reply_text else '…'}")
    return CommandResult(output="\n".join(lines))
