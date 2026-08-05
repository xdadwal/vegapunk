"""Tests for backend selection — no network, no credentials.

``create_backend`` is the one place a provider name turns into something
runnable, so what's pinned here is the wiring: that each name resolves to the
right logpose provider carrying the config's own base_url/model/window, that an
unknown name and a bad effort level fail with messages the CLI can print
verbatim, and that building a backend stays offline (logpose resolves providers
lazily, and a test suite that quietly dialed api.anthropic.com would be a bug).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from vegapunk.backend import (
    EFFORT_LEVELS,
    Backend,
    create_backend,
    current_effort,
    validate_effort,
    with_effort,
)
from vegapunk.config import config


def test_a_backend_can_spawn_a_second_independent_provider():
    # The titling agent needs its own: two agents run on two event loops, and a
    # provider's client belongs to whichever touched it first.
    backend = create_backend("local")

    spawned = backend.spawn_provider()

    assert spawned is not backend.provider
    assert spawned.model_default == backend.provider.model_default


def test_local_resolves_the_docker_provider_from_config():
    cfg = replace(config, base_url="http://elsewhere:9999/v1", model="ai/qwen3", context_window=8192)

    backend = create_backend("local", cfg)

    assert backend.provider.name == "docker"
    assert backend.provider.model_default == "ai/qwen3"
    assert backend.model_label == "ai/qwen3"
    assert backend.context_window == 8192
    assert backend.extra == {}  # local has no effort setting


def test_local_sends_no_credential_to_localhost(monkeypatch):
    # DockerModelsProvider disables api_key_env for this reason; pinned on the
    # actual request headers, because the key is private and a getattr probe
    # would pass just as happily against a provider that was leaking it.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-be-sent")

    backend = create_backend("local")

    assert "Authorization" not in backend.provider._headers()


def test_local_caps_output_so_a_long_answer_is_not_cut_off():
    # The provider's own ceiling is low enough to truncate a real reply; config
    # names the working budget instead.
    cfg = replace(config, max_output_tokens=12345)

    assert create_backend("local", cfg).provider.max_tokens == 12345
    assert create_backend("claude", cfg).provider.max_tokens == 12345


def test_claude_resolves_the_anthropic_provider_with_the_configured_model():
    cfg = replace(config, claude_model="claude-sonnet-5", claude_context_window=123456)

    backend = create_backend("claude", cfg)

    assert backend.provider.name == "anthropic"
    assert backend.provider.model_default == "claude-sonnet-5"
    assert backend.model_label == "claude-sonnet-5"
    assert backend.context_window == 123456


def test_an_empty_claude_model_defers_to_the_provider_rather_than_naming_a_default():
    # Vegapunk must not carry its own copy of the default model id: the label
    # has to be whatever the provider will actually request.
    from logpose.providers.anthropic import DEFAULT_MODEL

    backend = create_backend("claude", replace(config, claude_model=""))

    assert backend.model_label == DEFAULT_MODEL
    assert backend.provider.model_default == DEFAULT_MODEL


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("opus", "claude-opus-5"),
        ("sonnet", "claude-sonnet-5"),
        # Missing from the alias table meant `/model claude fable` went out as
        # the literal word and came back 404 "model: fable".
        ("fable", "claude-fable-5"),
        ("mythos", "claude-mythos-5"),
        ("haiku", "claude-haiku-4-5"),
    ],
)
def test_short_model_names_expand_to_full_ids(alias, expected):
    # The Claude Code CLI took these; the raw Messages API does not, so an
    # existing VEGAPUNK_CLAUDE_MODEL=sonnet would 404 on its first turn.
    backend = create_backend("claude", replace(config, claude_model=alias))

    assert backend.provider.model_default == expected
    assert backend.model_label == expected


def test_a_full_model_id_is_passed_through_untouched():
    cfg = replace(config, claude_model="claude-opus-4-8")

    assert create_backend("claude", cfg).provider.model_default == "claude-opus-4-8"


def test_unknown_provider_name_raises_with_the_valid_names():
    with pytest.raises(ValueError, match="expected 'local' or 'claude'"):
        create_backend("gpt")


# ---------------------------------------------------------------------------
# effort
# ---------------------------------------------------------------------------


def test_effort_travels_as_the_api_s_own_output_config():
    cfg = replace(config, claude_effort="xhigh")

    backend = create_backend("claude", cfg)

    assert backend.extra == {"output_config": {"effort": "xhigh"}}


def test_an_unset_effort_sends_nothing_rather_than_pinning_a_default():
    # Sending nothing leaves the API on its own default; hardcoding today's
    # default here would start lying the moment that default moved.
    cfg = replace(config, claude_effort="")

    assert create_backend("claude", cfg).extra == {}


@pytest.mark.parametrize("level", EFFORT_LEVELS)
def test_every_documented_effort_level_is_accepted(level):
    assert validate_effort(level) == level
    assert current_effort(with_effort(create_backend("claude"), level)) == level


def test_a_bad_effort_level_names_the_valid_ones():
    with pytest.raises(ValueError, match="low, medium, high, xhigh, max"):
        validate_effort("turbo")


def test_a_bad_effort_level_in_the_environment_surfaces_from_create_backend():
    # /model catches ValueError and prints it, so a junk VEGAPUNK_CLAUDE_EFFORT
    # has to arrive as one rather than as a traceback.
    cfg = replace(config, claude_effort="turbo")

    with pytest.raises(ValueError, match="Unknown effort level"):
        create_backend("claude", cfg)


def test_switching_effort_keeps_the_same_provider_instance():
    # Load-bearing, not an optimization: a provider's HTTP client binds to the
    # event loop that first touched it, so handing /effort a *new* provider
    # would strand the old client on a loop the session is about to close.
    backend = create_backend("claude")

    switched = with_effort(backend, "low")

    assert switched.provider is backend.provider
    assert switched.model_label == backend.model_label
    assert switched.context_window == backend.context_window


def test_clearing_effort_removes_the_parameter():
    assert with_effort(create_backend("claude"), "").extra == {}


def test_local_declares_that_it_has_no_effort_setting():
    assert create_backend("local").supports_effort is False
    assert create_backend("claude").supports_effort is True


# ---------------------------------------------------------------------------
# per-model capabilities
#
# "claude" is not one model: the older families answer `output_config.effort`
# and adaptive thinking with a 400 that kills the turn, so what a backend
# advertises has to follow the model, not the provider.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["haiku", "claude-sonnet-4-5-20250929", "claude-opus-4-1"])
def test_a_model_that_cannot_take_effort_says_so_instead_of_sending_it(model):
    cfg = replace(config, claude_model=model, claude_effort="high")

    backend = create_backend("claude", cfg)

    # The env var is dropped rather than sent: the API answers it with "This
    # model does not support the effort parameter".
    assert backend.supports_effort is False
    assert backend.extra == {}


def test_effort_on_such_a_model_is_refused_by_name():
    backend = create_backend("claude", replace(config, claude_model="haiku"))

    with pytest.raises(ValueError, match="claude-haiku-4-5 has no effort setting"):
        with_effort(backend, "high")


def test_a_junk_effort_level_is_still_named_even_on_a_model_without_effort():
    # Validation of the *level* comes first: a typo in VEGAPUNK_CLAUDE_EFFORT is
    # worth saying out loud whichever model is selected.
    cfg = replace(config, claude_model="haiku", claude_effort="turbo")

    with pytest.raises(ValueError, match="Unknown effort level"):
        create_backend("claude", cfg)


def test_opus_4_5_keeps_its_effort_setting():
    # The odd one out: no adaptive thinking, but effort works.
    cfg = replace(config, claude_model="claude-opus-4-5", claude_effort="high")

    backend = create_backend("claude", cfg)

    assert backend.supports_effort is True
    assert backend.extra == {"output_config": {"effort": "high"}}


@pytest.mark.parametrize("model", ["claude-opus-4-5", "haiku", "claude-sonnet-4-5"])
def test_models_without_adaptive_thinking_are_asked_for_none(model):
    # logpose requests adaptive thinking by default, which these answer with
    # "adaptive thinking is not supported on this model" — every turn, so the
    # model is unusable until the parameter is left off.
    backend = create_backend("claude", replace(config, claude_model=model))

    assert backend.provider._thinking is None


@pytest.mark.parametrize("model", ["", "opus", "fable", "claude-sonnet-4-6", "claude-whatever-9"])
def test_current_models_still_get_adaptive_thinking(model):
    # Unknown ids count as current, so a newly released model works here
    # without a code change.
    backend = create_backend("claude", replace(config, claude_model=model))

    assert backend.provider._thinking == {"type": "adaptive", "display": "summarized"}
    assert backend.supports_effort is True


def test_setting_effort_on_local_is_refused_rather_than_sent():
    # extra is merged into the request body verbatim, so accepting this would
    # ship an Anthropic-only parameter to the local server.
    with pytest.raises(ValueError, match="no effort setting"):
        with_effort(create_backend("local"), "high")


def test_current_effort_is_empty_for_a_backend_that_has_none():
    assert current_effort(create_backend("local")) == ""


def test_current_effort_ignores_an_unrecognized_extra():
    # extra is a general escape hatch; reading effort out of it must not throw
    # when it holds something else.
    backend = Backend(
        provider=create_backend("local").provider,
        model_label="x",
        context_window=0,
        extra={"tool_choice": {"type": "any"}},
    )

    assert current_effort(backend) == ""


# ---------------------------------------------------------------------------
# offline construction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["local", "claude"])
def test_building_a_backend_touches_no_network_and_reads_no_credential(name, monkeypatch):
    import socket

    def _no_network(*args, **kwargs):
        raise AssertionError("create_backend must not open a socket")

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    backend = create_backend(name)

    assert backend.provider.model_default
