"""Which model Vegapunk talks to, and how it's addressed.

A ``Backend`` is a logpose provider plus the facts the interface needs about it
that the provider itself doesn't carry: what to call the model in the toolbar,
how big its context window is (for the fullness gauge), and whether it can take
an effort setting. ``create_backend`` is the single selection point — the CLI's
startup default, the ``/model`` command, and the scheduler worker all come
through here.

Providers are resolved by name and import their SDK lazily, so building one never
touches the network or reads a credential; a local-only setup never pays for the
Anthropic path.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from logpose import Provider, resolve

from .config import Config, config

# Anthropic's effort levels, in order. These are the API's own values for
# ``output_config.effort`` — /effort passes the user's word straight through, so
# there is no mapping to keep in sync, only this list to validate against.
EFFORT_LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

# Short model names the Claude Code CLI accepted, which the raw Messages API
# does not. Kept so an existing VEGAPUNK_CLAUDE_MODEL=sonnet (or `/model claude
# opus`) keeps working instead of 404ing on its first turn. Anything else is
# passed through untouched — this is a compatibility shim for two words, not a
# model registry, and a full id always wins.
_MODEL_ALIASES = {
    "opus": "claude-opus-5",
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
}


@dataclass(frozen=True)
class Backend:
    """A model Vegapunk can run on, ready to hand to a logpose ``Agent``.

    Attributes:
        provider: The logpose provider driving the model.
        model_label: What to call it in the toolbar and in /model's output.
        context_window: Its context window in tokens, for the fullness gauge.
            0 means unknown — the gauge then shows tokens without a percentage.
        supports_effort: Whether this backend takes an effort level at all.
            False for local, so /effort can say so rather than silently sending
            an Anthropic-only parameter to a local server.
        extra: Provider-specific request parameters merged into every turn.
            Carries the effort setting for the Claude backend; empty for local.
        spawn_provider: Builds a *second*, independent provider for the same
            model. Needed because a provider's HTTP client binds itself to the
            event loop that first uses it, so two agents (the conversation and
            the throwaway one that titles it) cannot share one.
    """

    provider: Provider
    model_label: str
    context_window: int
    supports_effort: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
    spawn_provider: Callable[[], Provider] = field(default=lambda: _no_spawn())


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


def _effort_extra(level: str) -> dict[str, Any]:
    """The wire shape for an effort level, or nothing when it's unset.

    Empty means "don't send the parameter", which leaves the API on its own
    default — not the same as pinning that default here, which would start
    lying the moment the API's default moved.
    """
    if not level:
        return {}
    return {"output_config": {"effort": validate_effort(level)}}


def create_backend(name: str, cfg: Config = config) -> Backend:
    """Build the backend for a provider name ("local" or "claude").

    Raises ``ValueError`` for an unknown name, and for a malformed effort level
    — /model surfaces both as plain text rather than a traceback.
    """
    if name == "local":
        # Docker Model Runner speaks OpenAI-compatible HTTP and needs no
        # credential, so there's nothing to pass but where it lives and how much
        # it may generate.
        def spawn_local() -> Provider:
            return resolve(
                "docker",
                base_url=cfg.base_url,
                model=cfg.model,
                max_tokens=cfg.max_output_tokens,
            )

        return Backend(
            provider=spawn_local(),
            model_label=cfg.model,
            context_window=cfg.context_window,
            spawn_provider=spawn_local,
        )
    if name == "claude":
        # An empty claude_model means "whatever the provider defaults to": don't
        # name a default here, ask the provider what its is, so the label can
        # never drift from the model actually being requested.
        kwargs: dict[str, Any] = {"max_tokens": cfg.max_output_tokens}
        if cfg.claude_model:
            kwargs["model_default"] = _MODEL_ALIASES.get(cfg.claude_model, cfg.claude_model)

        def spawn_claude() -> Provider:
            return resolve("anthropic", **kwargs)

        provider = spawn_claude()
        return Backend(
            provider=provider,
            model_label=provider.model_default,
            context_window=cfg.claude_context_window,
            supports_effort=True,
            extra=_effort_extra(cfg.claude_effort),
            spawn_provider=spawn_claude,
        )
    raise ValueError(f"Unknown provider {name!r} — expected 'local' or 'claude'.")


def with_effort(backend: Backend, level: str) -> Backend:
    """The same backend at a different effort level (the /effort command).

    Returns a new Backend — the provider instance is shared, not rebuilt, so
    switching effort doesn't drop the HTTP connection pool underneath it.
    Raises ``ValueError`` on a backend that has no effort setting: ``extra`` is
    merged into the request body verbatim, so quietly accepting the level would
    ship an Anthropic-only parameter to a local server.
    """
    if not backend.supports_effort:
        raise ValueError(f"{backend.model_label} has no effort setting — /model claude first.")
    return replace(backend, extra=_effort_extra(level))


def current_effort(backend: Backend) -> str:
    """The backend's effort level, or "" when it isn't set.

    Reads the wire shape back rather than storing the level twice, so the two
    can't disagree. "" on an effort-capable backend means the API's own default;
    check ``supports_effort`` to tell that from "this model has no such setting".
    """
    output_config = backend.extra.get("output_config")
    if not isinstance(output_config, dict):
        return ""
    effort = output_config.get("effort")
    return effort if isinstance(effort, str) else ""
