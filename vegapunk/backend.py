"""Which model Vegapunk talks to, and how it's addressed.

A ``Backend`` is a logpose provider plus the facts the interface needs about it
that the provider itself doesn't carry: what to call the model in the toolbar,
how big its context window is (for the fullness gauge), and whether it can take
an effort setting. ``create_backend`` is the single selection point — the CLI's
startup default, the ``/model`` command, and the scheduler worker all come
through here.

The list of backends is logpose's, not ours. ``provider_catalog()`` is the
source of truth for which names exist, what each one authenticates with, and
whether it is an officially supported integration; Vegapunk adds only what
logpose has no opinion about — a context window per provider, two legacy
aliases, and the Claude model quirks below. That is deliberate: a provider added
to logpose shows up in ``/model`` here without a code change.

Providers are resolved by name and import their SDK lazily, so building one never
touches the network or reads a credential; a local-only setup never pays for the
Anthropic path.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from logpose import (
    LogposeError,
    Provider,
    ProviderInfo,
    provider_catalog,
    provider_info,
    resolve,
)

from .config import Config, config

# Effort levels, in order. Both wire APIs that take an effort setting use these
# same five words (Anthropic's ``output_config.effort`` and the Responses API's
# ``reasoning.effort``), so /effort passes the user's word straight through and
# there is no mapping to keep in sync — only this list to validate against.
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

# Vegapunk's own names for two logpose providers, kept because they predate the
# catalog: `local` is what the docs, VEGAPUNK_PROVIDER, and every saved
# `/schedule --model local` spec already say, and `claude` meant the
# subscription path before logpose split it into a provider per credential.
# Every other name is logpose's own, so this table does not grow.
ALIASES = {
    "local": "docker",
    "claude": "claude-code",
}

# Context window per provider, for the toolbar's fullness gauge. logpose doesn't
# carry this — it's a property of the model, not the wire API, and no backend
# reports it. 0 means unknown, and the gauge then shows tokens without a
# percentage rather than a made-up one. The two `cfg` entries are resolved per
# call so the existing VEGAPUNK_*_CONTEXT_WINDOW overrides keep working.
_CONTEXT_WINDOWS: dict[str, str] = {
    "docker": "context_window",
    "claude-code": "claude_context_window",
    "anthropic": "claude_context_window",
    "codex": "codex_context_window",
    "openai": "codex_context_window",
}

# Short model names the Claude Code CLI accepted, which the raw Messages API
# does not. Kept so an existing VEGAPUNK_CLAUDE_MODEL=sonnet (or `/model claude
# opus`) keeps working instead of 404ing on its first turn. Anything else is
# passed through untouched — this is a compatibility shim for a handful of
# words, not a model registry, and a full id always wins.
_MODEL_ALIASES = {
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "fable": "claude-fable-5",
    "mythos": "claude-mythos-5",
    "haiku": "claude-haiku-4-5",
}

# Not every Claude model takes every parameter, and the API answers one it
# doesn't with a 400 that kills the turn. Both lists name what a model *lacks*,
# so an unrecognized (i.e. newly released) id is assumed to be current and works
# without a code change here; only the older families need naming. Matched as a
# prefix, so dated snapshots (…-4-5-20250929) resolve like their base id.
#
# - no adaptive thinking: the API rejects {"type": "adaptive"} outright, so the
#   thinking parameter is omitted for these models.
# - no effort: `output_config.effort` comes back "This model does not support
#   the effort parameter", so /effort must report it as unsupported rather than
#   send it. (opus-4-5 is the odd one: effort yes, adaptive thinking no.)
_NO_ADAPTIVE_THINKING = (
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "claude-opus-4-1",
    "claude-3",
)
_NO_EFFORT = (
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "claude-opus-4-1",
    "claude-3",
)


def _resolve_model(name: str) -> str:
    """Expand a short model name; "" means "whatever the provider defaults to"."""
    return _MODEL_ALIASES.get(name, name)


def supports_adaptive_thinking(model: str) -> bool:
    """Whether ``model`` accepts ``thinking={"type": "adaptive"}``.

    "" (the provider's own default model) is current by definition, so it does.
    """
    return not model.startswith(_NO_ADAPTIVE_THINKING)


def supports_effort(model: str) -> bool:
    """Whether ``model`` accepts ``output_config.effort``."""
    return not model.startswith(_NO_EFFORT)


def canonical_name(name: str) -> str:
    """Vegapunk's name for a backend -> logpose's, expanding the two aliases."""
    return ALIASES.get(name.lower(), name.lower())


def backend_names() -> list[str]:
    """Every name /model accepts: Vegapunk's aliases first, then logpose's.

    Reads the catalog rather than a list here, so a provider added to logpose is
    selectable as soon as the pin moves. logpose's *own* aliases are included
    too (``docker-models`` for ``docker``) — resolve accepts them, so refusing
    them here would be Vegapunk inventing a restriction of its own.
    """
    catalog = sorted(
        name for info in provider_catalog() for name in (info.name, *info.aliases)
    )
    return sorted(ALIASES) + catalog


def describe(name: str) -> ProviderInfo:
    """The catalog entry for a backend name, or ``ValueError`` if there is none.

    The message names every valid spelling, because both callers (/model and the
    scheduler's ``provider[:model]`` spec) surface it straight to the user.
    """
    try:
        return provider_info(canonical_name(name))
    except LogposeError as exc:
        # Re-raised as ValueError because that is the channel /model, /schedule,
        # and the worker already surface as plain text rather than a traceback.
        raise ValueError(
            f"Unknown provider {name!r} — expected one of: {', '.join(backend_names())}."
        ) from exc


# Which config field carries a model override, per wire API. Keyed by API
# rather than provider name so `anthropic` and `claude-code` share one setting
# (they are the same models behind different credentials), as do the two
# Responses backends.
_MODEL_FIELDS = {
    "messages": "claude_model",
    "responses": "codex_model",
    "chat-completions": "model",
}


def with_model(cfg: Config, name: str, model: str) -> Config:
    """``cfg`` with ``model`` applied to whichever field that backend reads.

    So ``/model codex gpt-5.1`` and ``/model claude opus`` are the same gesture,
    and the scheduler's ``codex:gpt-5.1`` spec means what it says instead of
    silently landing in the Claude setting. An empty ``model`` is a no-op.
    Raises ``ValueError`` for an unknown backend name.
    """
    api = describe(name).api
    if not model:
        return cfg
    return replace(cfg, **{_MODEL_FIELDS[api]: model})


@dataclass(frozen=True)
class Backend:
    """A model Vegapunk can run on, ready to hand to a logpose ``Agent``.

    Attributes:
        provider: The logpose provider driving the model.
        model_label: What to call it in the toolbar and in /model's output.
        context_window: Its context window in tokens, for the fullness gauge.
            0 means unknown — the gauge then shows tokens without a percentage.
        extra: Provider-specific request parameters merged into every turn.
            Carries the effort setting; empty when there is none to send.
        spawn_provider: Builds a *second*, independent provider for the same
            model. Needed because a provider's HTTP client binds itself to the
            event loop that first uses it, so two agents (the conversation and
            the throwaway one that titles it) cannot share one.
        effort_key: The request field this backend's effort rides in — empty
            when it has none, which is also what ``supports_effort`` reads. One
            field rather than two: a backend that claimed to support effort but
            had nowhere to put it would accept ``/effort high`` and silently
            send nothing.
    """

    provider: Provider
    model_label: str
    context_window: int
    extra: dict[str, Any] = field(default_factory=dict)
    spawn_provider: Callable[[], Provider] = field(default=lambda: _no_spawn())
    effort_key: str = ""

    @property
    def supports_effort(self) -> bool:
        """Whether this backend takes an effort level at all.

        False for the local model and for the older Claude models, so /effort
        can say so rather than sending a parameter that comes back a 400.
        """
        return bool(self.effort_key)


def _no_spawn() -> Provider:
    """Default for a Backend built by hand (tests) rather than by name."""
    raise RuntimeError("this backend cannot spawn a second provider")


def validate_effort(level: str) -> str:
    """Return ``level`` if it's a real effort level, else raise ``ValueError``.

    The message names the valid levels, because the two callers (/effort and the
    VEGAPUNK_CLAUDE_EFFORT env var) both surface it straight to the user.
    """
    if level not in EFFORT_LEVELS:
        raise ValueError(
            f"Unknown effort level {level!r} — expected one of: {', '.join(EFFORT_LEVELS)}."
        )
    return level


def _effort_key(api: str) -> str:
    """The request field an effort level rides in on this wire API, "" if none.

    Derived from the API rather than the provider name: every Messages backend
    spells it one way and every Responses backend the other, so a new provider
    on either API gets /effort with no change here. Chat Completions (the local
    model runner) has no such field at all.
    """
    return {"messages": "output_config", "responses": "reasoning"}.get(api, "")


def _effort_extra(key: str, level: str) -> dict[str, Any]:
    """The wire shape for an effort level, or nothing when it's unset.

    Empty means "don't send the parameter", which leaves the API on its own
    default — not the same as pinning that default here, which would start
    lying the moment the API's default moved.

    The Responses shape carries ``summary`` as well as ``effort``. logpose
    applies ``extra`` with a shallow ``dict.update``, so a ``reasoning`` block
    holding only the effort would replace the provider's own and drop the
    summary channel — silently costing every ``ThinkingDelta`` the trace shows.
    """
    if not key or not level:
        return {}
    validate_effort(level)
    if key == "reasoning":
        return {"reasoning": {"effort": level, "summary": "auto"}}
    return {"output_config": {"effort": level}}


def _context_window(name: str, cfg: Config) -> int:
    """The configured context window for a backend, or 0 when we don't know."""
    attribute = _CONTEXT_WINDOWS.get(name)
    return getattr(cfg, attribute) if attribute else 0


def create_backend(name: str, cfg: Config = config) -> Backend:
    """Build the backend for a provider name.

    Accepts any name in the logpose catalog plus Vegapunk's ``local`` and
    ``claude`` aliases. Raises ``ValueError`` for an unknown name, and for a
    malformed effort level — /model surfaces both as plain text rather than a
    traceback.
    """
    info = describe(name)
    kwargs: dict[str, Any] = {}

    # Only pass what the backend can actually use. The local runner needs to be
    # told where it lives; the hosted ones are found by their own env vars.
    if info.name in ("docker", "docker-models", "openai-compat"):
        kwargs["base_url"] = cfg.base_url
        model = cfg.model
    elif info.api == "messages":
        model = _resolve_model(cfg.claude_model)
    else:
        model = cfg.codex_model

    if model:
        # The Messages backends spell it `model_default`; the Responses and
        # Chat Completions ones spell it `model`.
        kwargs["model_default" if info.api == "messages" else "model"] = model
    # The providers' own ceilings are low enough to cut a long answer off
    # mid-sentence. The Codex subscription endpoint rejects the field outright,
    # and logpose already omits it there, so passing it is safe everywhere.
    kwargs["max_tokens"] = cfg.max_output_tokens
    # logpose asks Anthropic for adaptive thinking by default; models that
    # predate it 400 on the parameter, so those get none at all.
    if info.api == "messages" and not supports_adaptive_thinking(model):
        kwargs["thinking"] = None

    def spawn() -> Provider:
        return resolve(info.name, **kwargs)

    provider = spawn()
    # Label from the provider, not from `model`: an empty override means "use
    # the provider's default", and asking it keeps the toolbar from drifting
    # from the model actually being requested.
    label = provider.model_default or model or info.name

    key = _effort_key(info.api)
    # A configured effort is validated whatever the model (a typo in
    # VEGAPUNK_CLAUDE_EFFORT should be named as such), then dropped when the
    # model can't take it — /model claude haiku shouldn't fail because an env
    # var from an earlier model is still set.
    configured = cfg.claude_effort if info.api == "messages" else cfg.codex_effort
    effort = validate_effort(configured) if configured else ""
    takes_effort = bool(key) and (info.api != "messages" or supports_effort(label))
    key = key if takes_effort else ""
    return Backend(
        provider=provider,
        model_label=label,
        context_window=_context_window(info.name, cfg),
        extra=_effort_extra(key, effort),
        spawn_provider=spawn,
        effort_key=key,
    )


def with_effort(backend: Backend, level: str) -> Backend:
    """The same backend at a different effort level (the /effort command).

    Returns a new Backend — the provider instance is shared, not rebuilt, so
    switching effort doesn't drop the HTTP connection pool underneath it.
    Raises ``ValueError`` on a backend that has no effort setting: ``extra`` is
    merged into the request body verbatim, so quietly accepting the level would
    ship a parameter the server doesn't understand.
    """
    if not backend.supports_effort:
        raise ValueError(
            f"{backend.model_label} has no effort setting — "
            "switch to a model that has one, e.g. /model claude opus."
        )
    return replace(backend, extra=_effort_extra(backend.effort_key, level))


def current_effort(backend: Backend) -> str:
    """The backend's effort level, or "" when it isn't set.

    Reads the wire shape back rather than storing the level twice, so the two
    can't disagree. "" on an effort-capable backend means the API's own default;
    check ``supports_effort`` to tell that from "this model has no such setting".
    """
    block = backend.extra.get(backend.effort_key) if backend.effort_key else None
    if not isinstance(block, dict):
        return ""
    effort = block.get("effort")
    return effort if isinstance(effort, str) else ""
