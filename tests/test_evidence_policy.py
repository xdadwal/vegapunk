"""All composed agent prompts carry the same citation and ranking contract."""

import pytest

from vegapunk import prompt
from vegapunk.config import config


@pytest.mark.parametrize("role", ["default", "shaka", "lilith", "edison", "pythagoras", "atlas", "york"])
def test_main_and_delegate_prompts_share_evidence_policy(role):
    composed = prompt.system_prompt(config, mode="manual", agent_id=role, delegation=role == "default", include_memory=False)
    assert "Evidence and citations:" in composed
    assert "Direct support comes before source reputation" in composed
    assert "A citation is traceability, not proof" in composed
    assert "Do not invent citations" in composed


def test_journal_prompt_keeps_proportionate_evidence_guidance():
    composed = prompt.system_prompt(config, mode="manual", conversation_mode="journal")
    assert "Do not demand citations for personal experiences" in composed
