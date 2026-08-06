"""The REPL's slash commands — ``/help``, ``/save``, ``/sessions``,
``/new``, ``/exit``.

Mirrors the ``@tool`` registry (``tools/registry.py``): a ``@command`` decorator
registers a handler into ``REGISTRY``, so adding a command is one function and
``/help`` is generated from the registry. The CLI calls ``dispatch`` on each
``/``-prefixed line; non-command input is sent to the model instead.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable

from logpose import provider_catalog, provider_status

from . import db, menu, scheduler, session_store, skills, transcript
from .backend import (
    ALIASES,
    EFFORT_LEVELS,
    available_models,
    create_backend,
    current_effort,
    describe,
    resolve_model_choice,
    with_effort,
    with_model,
)
from .config import config
from .session import Session

# Sorted once: /model's listing shows each provider's Vegapunk alias beside it.
_ALIAS_ITEMS = sorted(ALIASES.items())

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
    scheduler: object | None = None
    scheduler_log: str | None = None


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


@command("new", "Start a fresh conversation", "reset")
def _new(ctx: CommandContext, arg: str) -> CommandResult:
    ctx.session.reset()
    ctx.current_name = None  # next turn auto-names a fresh saved session
    ctx.pending_skill = None  # a fresh conversation drops staged state too
    return CommandResult(output="(new conversation)")


def _interactive() -> bool:
    """Whether there's a terminal to open a picker on.

    Gated on stdin specifically: a piped or scripted session (tests, CI, `echo
    /model | vegapunk`) has no one to arrow around a menu, and every command
    below keeps its plain-text behaviour there. That is also what keeps the
    non-interactive paths — which the scheduler and the test suite rely on —
    exactly as they were.
    """
    return sys.stdin.isatty()


def _pick(title: str, options: list[menu.Option]) -> str | None:
    """Open the picker, or return ``None`` when there is nothing to pick."""
    return menu.choose(title, options) if options else None


def _catalog_listing() -> str:
    """One line per selectable backend: name, what it authenticates with, and
    whether it can actually run right now.

    Readiness is asked of logpose rather than guessed, so a missing credential
    is reported here — at the moment you are choosing — instead of as an
    ``AuthError`` on your next message. It reads credential stores (including a
    Keychain subprocess on macOS), which is why /model pays for it only when
    called with no argument.
    """
    status = {s.name: s for s in asyncio.run(provider_status())}
    lines = []
    for info in sorted(provider_catalog(), key=lambda i: i.name):
        names = "|".join([info.name, *(a for a, t in _ALIAS_ITEMS if t == info.name)])
        ready = status.get(info.name)
        mark = "  " if ready is None or ready.ready else "· "
        note = "" if ready is None or ready.ready else f" — {ready.detail}"
        unsupported = "" if info.officially_supported else " [unofficial]"
        lines.append(f"  {mark}{names} ({info.credential}){unsupported}{note}")
    return "\n".join(lines)


@command("status", "Show backend, session, scheduler, and workspace status")
def _status(ctx: CommandContext, arg: str) -> CommandResult:
    if arg:
        return CommandResult(output="Usage: /status")
    provider = _live_backend_name(ctx)
    try:
        statuses = {item.name: item for item in asyncio.run(provider_status())}
        credential = statuses.get(provider)
        readiness = "ready" if credential is None or credential.ready else credential.detail
    except Exception as exc:  # noqa: BLE001 — status must not make the REPL unusable
        readiness = f"unavailable ({exc})"
    effort = current_effort(ctx.session.backend) or "API default"
    used = ctx.session.context_tokens
    window = ctx.session.context_window
    context = "unknown" if used is None else f"{used:,}/{window:,} tok" if window else f"{used:,} tok"
    if ctx.scheduler is None:
        worker = "not running"
    else:
        code = getattr(ctx.scheduler, "poll")()
        worker = "running" if code is None else f"exited ({code})"
    if ctx.scheduler_log:
        worker += f" · {ctx.scheduler_log}"
    return CommandResult(output="\n".join([
        f"Backend: {provider} · {readiness}",
        f"Model: {ctx.session.model_label}",
        f"Effort: {effort}",
        f"Context: {context}",
        f"Session: {ctx.current_name or 'unsaved'}",
        f"Scheduler: {worker}",
        f"Workspace: {config.workspace_root}",
    ]))


@command("model", "Show or switch the model: /model [provider [model]]")
def _model(ctx: CommandContext, arg: str) -> CommandResult:
    if not arg and _interactive():
        return _pick_backend(ctx)
    if not arg:
        return CommandResult(
            output=f"Active: {ctx.session.model_label}\nAvailable:\n{_catalog_listing()}"
        )
    tokens = arg.split()
    provider = tokens[0].lower()
    model = tokens[1] if len(tokens) == 2 else ""
    if len(tokens) > 2:
        return CommandResult(output="Usage: /model [provider [model]]")
    # The choice is checked against what the backend serves, so a typo is caught
    # here rather than as a 404 on your next message.
    return _switch(ctx, provider, model)



def _pick_backend(ctx: CommandContext) -> CommandResult:
    """Choose a backend from a menu, marking the live one and what can't run."""
    status = {s.name: s for s in asyncio.run(provider_status())}
    live = _live_backend_name(ctx)
    options = []
    for info in sorted(provider_catalog(), key=lambda i: i.name):
        alias = next((a for a, t in _ALIAS_ITEMS if t == info.name), "")
        ready = status.get(info.name)
        detail = [info.credential if alias == "" else f"{alias} · {info.credential}"]
        if not info.officially_supported:
            detail.append("unofficial")
        if ready is not None and not ready.ready:
            detail.append(ready.detail)
        options.append(
            menu.Option(
                value=info.name,
                label=info.name,
                detail=" · ".join(detail),
                active=info.name == live,
            )
        )
    chosen = _pick("choose a backend", options)
    if chosen is None:
        return CommandResult(output="(unchanged)")
    try:
        served = available_models(chosen)
    except Exception:
        served = []
    return _pick_model(ctx, chosen, served) if served else _switch(ctx, chosen, "")


def _pick_model(ctx: CommandContext, provider: str, served: list[str]) -> CommandResult:
    """Choose a model on ``provider`` from a menu, marking the live one."""
    live = ctx.session.model_label
    options = [menu.Option(value=name, label=name, active=name == live) for name in served]
    chosen = _pick(f"choose a model on {provider}", options)
    if chosen is None:
        return CommandResult(output="(unchanged)")
    return _switch(ctx, provider, chosen)


def _switch(ctx: CommandContext, provider: str, model: str) -> CommandResult:
    """Build and install a backend, carrying the session's effort choice over.

    The one place /model, the two pickers, and /models all end up, so a switch
    means the same thing however it was asked for.
    """
    try:
        chosen = resolve_model_choice(provider, model)
        backend = create_backend(provider, with_model(config, provider, chosen))
        # Carry a /effort choice across claude→claude swaps (a claude→local→claude
        # round trip loses it — the local backend has nowhere to hold it).
        effort = current_effort(ctx.session.backend)
        if effort and backend.supports_effort:
            backend = with_effort(backend, effort)
    except ValueError as exc:
        return CommandResult(output=str(exc))
    ctx.session.swap_backend(backend)
    return CommandResult(
        output=f"(model switched to {backend.model_label} — the conversation continues)"
    )


def _live_backend_name(ctx: CommandContext) -> str:
    """logpose's name for the backend this session is running on."""
    return ctx.session.backend.provider.name


def _models(ctx: CommandContext, arg: str) -> CommandResult:
    """What this backend will actually answer to, asked of the backend itself.

    Defaults to the live one, so `/models` answers "what else could I switch
    to right now" without naming anything. The list is one network round trip,
    cached for the session.
    """
    provider = arg.split()[0].lower() if arg else _live_backend_name(ctx)
    try:
        # Validated first and separately, so an unknown name is reported as
        # such rather than folded into "couldn't list its models" below.
        known = describe(provider).name
    except ValueError as exc:
        return CommandResult(output=str(exc))
    try:
        served = available_models(provider)
    except Exception as exc:  # noqa: BLE001 — a backend that won't answer isn't fatal
        return CommandResult(output=f"({provider} couldn't list its models: {exc})")
    if not served:
        return CommandResult(output=f"({provider} reported no models)")
    if _interactive():
        return _pick_model(ctx, provider, served)
    live = ctx.session.model_label
    lines = [f"  {'*' if name == live else ' '} {name}" for name in served]
    return CommandResult(output=f"{known} serves:\n" + "\n".join(lines))



def _pick_session(ctx: CommandContext) -> CommandResult:
    """Choose a saved conversation to resume, newest first."""
    rows = session_store.list_sessions()
    if not rows:
        return CommandResult(output="(no saved sessions)")
    options = [
        menu.Option(
            value=name,
            label=name,
            detail=f"{turns} turns · {_local_stamp(updated_at)}",
            active=name == ctx.current_name,
        )
        for name, turns, updated_at in rows
    ]
    chosen = _pick("resume which conversation?", options)
    if chosen is None:
        return CommandResult(output="(nothing loaded)")
    return _resume(ctx, chosen)


def _pick_skill() -> str | None:
    """Choose a skill to stage, showing each one's summary as its detail."""
    available = skills.list_skills()
    if not available:
        return None
    options = [menu.Option(value=s.name, label=s.name, detail=s.description) for s in available]
    return _pick("stage which skill?", options)


def _pick_effort(ctx: CommandContext) -> CommandResult:
    """Choose a reasoning-effort level, marking the one in force."""
    live = current_effort(ctx.session.backend)
    options = [
        menu.Option(value=level, label=level, active=level == live) for level in EFFORT_LEVELS
    ]
    chosen = _pick(f"effort for {ctx.session.model_label}", options)
    if chosen is None:
        return CommandResult(output="(unchanged)")
    return _effort(ctx, chosen)


@command("effort", "Show or set reasoning effort: /effort [low|medium|high|xhigh|max]")
def _effort(ctx: CommandContext, arg: str) -> CommandResult:
    backend = ctx.session.backend
    # Asked of the backend rather than duck-typed: a backend that takes no
    # effort setting says so, which is a different thing from one that takes it
    # and is currently unset.
    if not backend.supports_effort:
        # Names the live model: it's no longer only the local one that has no
        # effort setting — the older Claude models 400 on the parameter too.
        return CommandResult(
            output=f"({backend.model_label} has no effort setting — "
            "switch to a model that has one, e.g. /model claude opus)"
        )
    if not arg and _interactive():
        return _pick_effort(ctx)
    if not arg:
        # Unset means we send no effort parameter at all, leaving the API on its
        # own default rather than one we picked.
        return CommandResult(output=f"Effort: {current_effort(backend) or 'the API default'}")
    try:
        ctx.session.set_effort(arg.lower())
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


def _resume(ctx: CommandContext, arg: str) -> CommandResult:
    name = session_store.slugify(arg)
    if not name:
        if _interactive():
            return _pick_session(ctx)
        return CommandResult(output="Usage: /sessions <name>")
    try:
        messages = session_store.load_session(name)
    except session_store.SessionNotFound:
        return CommandResult(output=f"No session '{name}'.\n{_format_sessions()}")
    except db.StoreError as exc:
        return CommandResult(output=f"Could not load '{name}': {exc}")
    try:
        ctx.session.restore(messages)
    except ValueError as exc:
        # A blob that got past the format check but still won't parse — a
        # half-written row, or a block type this version doesn't know. Commands
        # are dispatched outside the REPL's error handler, so an escaping
        # exception would take the whole session down over one bad row.
        return CommandResult(output=f"Could not resume '{name}': {exc}")
    ctx.current_name = name
    ctx.pending_skill = None  # staged state belongs to the conversation it was staged in
    return CommandResult(
        output=f"Resumed '{name}' ({transcript.count_user_turns(messages)} turns)."
    )


@command("sessions", "Resume, list, or remove conversations: /sessions [name | remove <name>]")
def _sessions(ctx: CommandContext, arg: str) -> CommandResult:
    sub, _, rest = arg.partition(" ")
    sub = sub.strip().lower()
    if not sub:
        return _pick_session(ctx) if _interactive() else CommandResult(output=_format_sessions())
    if sub == "remove":
        name = session_store.slugify(rest)
        if not name:
            return CommandResult(output="Usage: /sessions remove <name>")
        try:
            if not session_store.exists(name):
                return CommandResult(output=f"No session '{name}' to remove.\n{_format_sessions()}")
            session_store.delete_session(name)
        except db.StoreError as exc:
            return CommandResult(output=f"Could not remove '{name}': {exc}")
        if ctx.current_name == name:
            # The live conversation's saved copy is gone; the next turn re-saves
            # it under a fresh name rather than resurrecting the deleted one.
            ctx.current_name = None
        return CommandResult(output=f"Removed session '{name}'.")
    return _resume(ctx, arg)


def _format_tasks() -> str:
    tasks = scheduler.list_tasks()
    if not tasks:
        return "(no scheduled tasks)"
    lines = []
    for t in tasks:
        # last_status is None until the task has run once; show "pending" then.
        status = t.last_status or "pending"
        lines.append(
            f"  {t.id[:8]}  every {t.interval_seconds}s  next {_local_stamp(t.next_run_at)}  "
            f"[{status}]  {_oneline(t.prompt)}"
        )
    return "\n".join(lines)


@command("schedule", "Manage scheduled tasks: /schedule [list | add <seconds> <prompt> | remove <id>]")
def _schedule(ctx: CommandContext, arg: str) -> CommandResult:
    sub, _, rest = arg.partition(" ")
    sub = sub.strip().lower()
    if sub in ("", "list"):
        return CommandResult(output=_format_tasks())
    if sub == "add":
        # "<seconds> <prompt>": the first token is the interval, the rest is the
        # prompt. Validation of the interval's floor and the prompt's emptiness
        # lives in scheduler.add_task; here we just parse and hand off.
        interval_str, _, prompt = rest.strip().partition(" ")
        if not interval_str or not prompt.strip():
            return CommandResult(output="Usage: /schedule add <seconds> <prompt>")
        try:
            interval = int(interval_str)
        except ValueError:
            return CommandResult(
                output="Usage: /schedule add <seconds> <prompt>  (seconds must be a whole number)"
            )
        return CommandResult(output=scheduler.add_task(prompt, interval))
    if sub == "remove":
        return CommandResult(output=scheduler.remove_task(rest))
    return CommandResult(output="Usage: /schedule [list | add <seconds> <prompt> | remove <id>]")


@command("skill", "Stage a skill for your next message: /skill <name>")
def _skill(ctx: CommandContext, arg: str) -> CommandResult:
    """Force a skill by hand — for when the model doesn't reach for it. The
    body rides the next message instead of a use_skill round-trip."""
    if not arg:
        names = ", ".join(s.name for s in skills.list_skills()) or "(none installed)"
        if _interactive():
            picked = _pick_skill()
            if picked is not None:
                return _skill(ctx, picked)
            return CommandResult(output="(nothing staged)")
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


@command("history", "Show the last few turns of this conversation: /history [n]")
def _history(ctx: CommandContext, arg: str) -> CommandResult:
    limit = 5
    if arg:
        try:
            limit = max(1, int(arg))
        except ValueError:
            return CommandResult(output="Usage: /history [n]  (n = number of turns, default 5)")
    turns = transcript.recent_turns(ctx.session.messages, limit)
    if not turns:
        return CommandResult(output="(no conversation yet)")
    lines = []
    for user_text, reply_text in turns:
        lines.append(f"  you:  {_oneline(user_text)}")
        lines.append(f"  vega: {_oneline(reply_text) if reply_text else '…'}")
    return CommandResult(output="\n".join(lines))


@command("reason", "Show the last turn's reasoning")
def _reason(ctx: CommandContext, arg: str) -> CommandResult:
    if arg:
        return CommandResult(output="Usage: /reason")
    if not ctx.session.last_reasoning:
        return CommandResult(output="(no reasoning was available for the last turn)")
    return CommandResult(output=ctx.session.last_reasoning)
