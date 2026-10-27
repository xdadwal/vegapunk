"""Z.ai stays separate from local embeddings and other provider settings."""

from dataclasses import replace

import pytest

from vegapunk.backend import create_backend, current_effort, with_effort, with_model
from vegapunk.config import config


def test_zai_uses_coding_endpoint_and_its_own_model_settings():
    backend = create_backend("zai")
    assert backend.model_label == "glm-5.3-flash"
    assert backend.provider.base_url == "https://api.z.ai/api/coding/paas/v4"
    assert backend.extra == {"reasoning_effort": "low"}
    assert current_effort(backend) == "low"
    changed = with_model(config, "zai", "glm-5.3")
    assert create_backend("zai", changed).model_label == "glm-5.3"
    assert changed.model == config.model and changed.base_url == config.base_url
    assert create_backend("local", changed).provider.base_url == config.base_url


@pytest.mark.parametrize("level", ["low", "high", "max"])
def test_supported_zai_effort_roundtrips(level):
    backend = with_effort(create_backend("zai"), level)
    assert backend.extra == {"reasoning_effort": level}
    assert current_effort(backend) == level


@pytest.mark.parametrize("level", ["medium", "xhigh"])
def test_unsupported_zai_effort_is_rejected(level):
    with pytest.raises(ValueError, match="low.*high.*max"):
        with_effort(create_backend("zai"), level)


def test_zai_does_not_inherit_codex_or_claude_effort():
    backend = create_backend("zai", replace(config, codex_effort="medium", claude_effort="xhigh"))
    assert current_effort(backend) == "low"


def test_effort_completion_tracks_provider_specific_choices():
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    from vegapunk.prompter import SlashCompleter

    choices = ["low", "high", "max"]
    completer = SlashCompleter(effort_levels=lambda: tuple(choices))
    def complete():
        return [item.text for item in completer.get_completions(Document("/effort "), CompleteEvent())]
    assert complete() == ["low", "high", "max"]
    choices[:] = ["low", "medium", "high", "xhigh", "max"]
    assert complete() == ["low", "medium", "high", "xhigh", "max"]
