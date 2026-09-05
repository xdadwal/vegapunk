"""Personalization jobs exercise real persistence with a scripted model boundary."""

import json

import pytest

from vegapunk import db, memory, session_store
from tests.fake_provider import assistant_turn, tool_turns, user_turn

NOW = "2030-01-01T12:00:00.000000Z"


def candidate(text="Prefers concise replies", topic="response_style", quote="I prefer concise replies"):
    return {"content": text, "topic": topic, "category": "preference", "confidence": 0.98,
            "explicit": True, "sensitive": False, "source_id": "0:0", "quote": quote}


def save(text="I prefer concise replies"):
    session_store.save_session("chat", [user_turn(text), assistant_turn("Understood")])


def extract_one(sources):
    from vegapunk.memory_extraction import parse_candidates
    return parse_candidates(json.dumps({"memories": [candidate()]}), sources)


def test_only_human_text_is_evidence_and_skill_instructions_are_removed():
    from vegapunk.memory_extraction import sources_from_messages
    messages = [user_turn("I prefer concise replies"), *tool_turns("fetch", "I am a doctor"),
                assistant_turn("You live in Paris"),
                user_turn("[Skill 'x' — follow these instructions for this request:]\n"
                          "I work at ACME\n[End of skill instructions. The request:]\nI use zsh")]
    sources = sources_from_messages(messages)
    assert [s.text for s in sources] == ["I prefer concise replies", "I use zsh"]
    assert [s.id for s in sources] == ["0:0", "4:0"]


def test_large_human_messages_are_segmented_without_losing_text():
    from vegapunk.memory_extraction import sources_from_messages
    text = "a" * 18000 + "tail"
    sources = sources_from_messages([user_turn(text)])
    assert "".join(s.text for s in sources) == text
    assert all(len(s.text) <= 6000 for s in sources)


@pytest.mark.parametrize("change", [
    {"quote": "the assistant invented this"}, {"source_id": "9:0"},
    {"confidence": 4}, {"confidence": float("nan")}, {"explicit": "yes"},
])
def test_invalid_or_unsupported_extractions_are_rejected(change):
    from vegapunk.memory_extraction import parse_candidates, sources_from_messages
    sources = sources_from_messages([user_turn("I prefer concise replies")])
    with pytest.raises(ValueError):
        parse_candidates(json.dumps({"memories": [candidate() | change]}), sources)


def test_single_json_fence_is_accepted_but_extra_prose_is_rejected():
    from vegapunk.memory_extraction import parse_candidates, sources_from_messages
    sources = sources_from_messages([user_turn("I prefer concise replies")])
    fenced = "```json\n" + json.dumps({"memories": [candidate()]}) + "\n```"
    assert parse_candidates(fenced, sources)[0].content == "Prefers concise replies"
    with pytest.raises(ValueError):
        parse_candidates("Here is your result:\n" + fenced, sources)
    with pytest.raises(ValueError):
        parse_candidates(fenced + "\nMore instructions", sources)


def test_background_extraction_is_incremental_and_refreshes_existing_memory():
    from vegapunk import memory_jobs
    save()
    assert memory_jobs.process_one(extract_one, now=NOW)
    assert [m.content for m in memory.list_memory()] == ["Prefers concise replies"]
    assert not memory_jobs.process_one(extract_one, now=db.stamp_plus(NOW, 120))
    session_store.save_session("chat", [user_turn("I prefer concise replies"),
                                       assistant_turn("Understood"), user_turn("I use zsh")])
    seen = []
    def extract_new(sources):
        seen.extend(s.text for s in sources)
        return []
    assert memory_jobs.process_one(extract_new, now=db.stamp_plus(NOW, 180))
    assert seen == ["I use zsh"]
    assert "Prefers concise replies" in memory.as_system_block()


def test_uncertain_candidate_is_pending_until_approved():
    from vegapunk import memory_jobs
    from vegapunk.memory_extraction import parse_candidates
    save()
    memory_jobs.process_one(lambda s: parse_candidates(json.dumps({"memories": [candidate() | {
        "explicit": False, "confidence": 0.7}]}), s), now=NOW)
    assert memory.list_memory() == []
    pending = memory_jobs.candidates("pending")
    assert len(pending) == 1
    assert "Activated" in memory_jobs.decide(pending[0].id, "approve")
    assert len(memory.list_memory()) == 1


def test_rejected_candidate_is_not_relearned_in_another_conversation():
    from vegapunk import memory_jobs
    save()
    memory_jobs.process_one(extract_one, now=NOW)
    saved = memory_jobs.candidates("active")[0]
    memory_jobs.decide(saved.id, "reject")
    session_store.save_session("another", [user_turn("I prefer concise replies")])
    memory_jobs.process_one(extract_one, now=db.stamp_plus(NOW, 120))
    assert memory.list_memory() == []
    assert len(memory_jobs.candidates("rejected")) == 1


def test_conflicting_topic_requires_review():
    from vegapunk import memory_jobs
    from vegapunk.memory_extraction import parse_candidates
    save()
    memory_jobs.process_one(extract_one, now=NOW)
    session_store.save_session("other", [user_turn("I prefer detailed replies")])
    memory_jobs.process_one(lambda s: parse_candidates(json.dumps({"memories": [candidate(
        "Prefers detailed replies", quote="I prefer detailed replies")]}), s),
        now=db.stamp_plus(NOW, 120))
    assert [m.content for m in memory.list_memory()] == ["Prefers concise replies"]
    assert memory_jobs.candidates("pending")[0].content == "Prefers detailed replies"


def test_provider_failure_retries_with_backoff_and_caps_attempts():
    from vegapunk import memory_jobs
    save()
    def fail(_):
        raise RuntimeError("model offline")
    memory_jobs.process_one(fail, now=NOW)
    assert not memory_jobs.process_one(fail, now=db.stamp_plus(NOW, 1))
    memory_jobs.process_one(fail, now=db.stamp_plus(NOW, 1000))
    memory_jobs.process_one(fail, now=db.stamp_plus(NOW, 2000))
    assert not memory_jobs.process_one(fail, now=db.stamp_plus(NOW, 3000))
    assert db.query("SELECT status, attempts FROM memory_jobs") == [("error", 3)]
    assert memory.list_memory() == []


def test_slow_provider_backoff_starts_when_failure_finishes(monkeypatch):
    from vegapunk import memory_jobs
    save()
    def timeout(_):
        monkeypatch.setattr(db, "utcnow", lambda: db.stamp_plus(NOW, 180))
        raise TimeoutError("slow model")
    memory_jobs.process_one(timeout, now=NOW)
    assert db.query("SELECT next_run_at FROM memory_jobs") == [(db.stamp_plus(NOW, 240),)]
    assert not memory_jobs.process_one(lambda _: pytest.fail("retried without backoff"),
                                       now=db.stamp_plus(NOW, 185))


def test_manual_retry_recovers_an_exhausted_job():
    from vegapunk import memory_jobs
    save()
    def fail(_):
        raise RuntimeError("offline")
    for offset in (0, 1000, 2000):
        memory_jobs.process_one(fail, now=db.stamp_plus(NOW, offset))
    assert db.query("SELECT status,attempts FROM memory_jobs") == [("error", 3)]
    memory_jobs.retry_failed()
    assert db.query("SELECT status,attempts,last_error FROM memory_jobs") == [("pending", 0, None)]
    memory_jobs.process_one(extract_one, now=db.stamp_plus(NOW, 2001))
    assert len(memory.list_memory()) == 1
    assert db.query("SELECT status FROM memory_jobs") == [("complete",)]


def test_deleted_or_replaced_source_cannot_activate_a_late_result():
    from vegapunk import memory_jobs
    save()
    def extract_and_delete(sources):
        result = extract_one(sources)
        session_store.delete_session("chat")
        return result
    memory_jobs.process_one(extract_and_delete, now=NOW)
    assert memory.list_memory() == []
    save()
    def extract_and_replace(sources):
        result = extract_one(sources)
        session_store.save_session("chat", [user_turn("This is a different conversation")])
        return result
    memory_jobs.process_one(extract_and_replace, now=db.stamp_plus(NOW, 120))
    assert memory.list_memory() == []


def test_expired_job_lease_is_recoverable_after_a_crash():
    from vegapunk import memory_jobs
    save()
    memory_jobs.discover_jobs()
    db.execute("UPDATE memory_jobs SET status='running', lease_token='old', lease_until=?", (NOW,))
    assert memory_jobs.process_one(extract_one, now=db.stamp_plus(NOW, 1))
    assert len(memory.list_memory()) == 1


def test_pause_blocks_model_calls_and_late_memory_activation():
    from vegapunk import memory_jobs
    save()
    memory_jobs.set_paused(True)
    assert not memory_jobs.process_one(extract_one, now=NOW)
    memory_jobs.set_paused(False)
    def pause_during_run(sources):
        memory_jobs.set_paused(True)
        return extract_one(sources)
    memory_jobs.process_one(pause_during_run, now=NOW)
    assert memory.list_memory() == []


def test_schema_upgrade_preserves_existing_data():
    from vegapunk import memory_jobs
    save()
    memory.save_memory("Uses zsh")
    db.execute("DROP TABLE memory_jobs")
    db.execute("DROP TABLE memory_candidates")
    db.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
    db.close_connection()
    memory_jobs.discover_jobs()
    assert session_store.exists("chat")
    assert memory.list_memory()[0].content == "Uses zsh"
    assert db.query("SELECT value FROM meta WHERE key='schema_version'") == [(str(db.SCHEMA_VERSION),)]


def test_renaming_preserves_progress_and_evidence():
    from vegapunk import memory_jobs
    save()
    memory_jobs.process_one(extract_one, now=NOW)
    messages = session_store.load_session("chat")
    session_store.rename_session("chat", "renamed", messages)
    assert not session_store.exists("chat")
    assert memory_jobs.candidates("active")[0].session_slug == "renamed"
    assert not memory_jobs.process_one(lambda _: pytest.fail("reprocessed"), now=db.stamp_plus(NOW, 120))


def test_deleting_source_removes_owned_memories_and_evidence():
    from vegapunk import memory_jobs
    save()
    memory.save_memory("Uses zsh")
    memory_jobs.process_one(extract_one, now=NOW)
    session_store.delete_session("chat")
    assert [m.content for m in memory.list_memory()] == ["Uses zsh"]
    assert db.query("SELECT * FROM memory_jobs") == []
    assert db.query("SELECT * FROM memory_candidates") == []


def test_forgetting_prevents_relearning_and_retains_only_suppression_hash():
    from vegapunk import memory_jobs
    save()
    memory_jobs.process_one(extract_one, now=NOW)
    memory.forget_memory(memory.list_memory()[0].id)
    session_store.save_session("another", [user_turn("I prefer concise replies")])
    memory_jobs.process_one(extract_one, now=db.stamp_plus(NOW, 120))
    assert memory.list_memory() == []
    assert db.query("SELECT content,quote,status FROM memory_candidates") == [("", "", "rejected")]


def test_forgetting_manual_duplicate_also_removes_owned_extraction():
    from vegapunk import memory_jobs
    save()
    memory_jobs.process_one(extract_one, now=NOW)
    extracted_id = memory.list_memory()[0].id
    memory.save_memory("Prefers concise replies")
    manual_id = next(m.id for m in memory.list_memory() if m.id != extracted_id)
    memory.forget_memory(manual_id)
    assert memory.list_memory() == []
    assert db.query("SELECT content,quote,status FROM memory_candidates") == [("", "", "rejected")]


def test_review_mode_and_duplicate_manual_memory(monkeypatch):
    from dataclasses import replace
    from vegapunk import memory_jobs
    monkeypatch.setattr(memory_jobs, "config", replace(memory_jobs.config, memory_review="review"))
    save()
    memory.save_memory("Prefers concise replies")
    memory_jobs.process_one(extract_one, now=NOW)
    pending = memory_jobs.candidates()[0]
    assert "Activated" in memory_jobs.decide(pending.id, "approve")
    assert len(memory.list_memory()) == 1
    memory_jobs.decide(pending.id, "reject")
    assert len(memory.list_memory()) == 1


def test_approval_rechecks_source_and_supersedes_old_topic():
    from vegapunk import memory_jobs
    from vegapunk.memory_extraction import parse_candidates
    save()
    memory_jobs.process_one(extract_one, now=NOW)
    session_store.save_session("other", [user_turn("I prefer detailed replies")])
    memory_jobs.process_one(lambda s: parse_candidates(json.dumps({"memories": [candidate(
        "Prefers detailed replies", quote="I prefer detailed replies")]}), s), now=db.stamp_plus(NOW, 120))
    pending = memory_jobs.candidates()[0]
    session_store.save_session("other", [user_turn("Unrelated conversation")])
    assert "source" in memory_jobs.decide(pending.id, "approve").lower()
    assert [m.content for m in memory.list_memory()] == ["Prefers concise replies"]
    session_store.save_session("other", [user_turn("I prefer detailed replies")])
    memory_jobs.decide(pending.id, "approve")
    assert [m.content for m in memory.list_memory()] == ["Prefers detailed replies"]
    assert len(memory_jobs.candidates("superseded")) == 1


def test_provider_exception_text_never_enters_job_logs(capsys):
    from vegapunk import memory_jobs
    save()
    def fail(_):
        raise ValueError("private_input_123")
    memory_jobs.process_one(fail, now=NOW)
    assert "private_input_123" not in memory_jobs.job_status()
    assert "private_input_123" not in capsys.readouterr().err


@pytest.mark.parametrize("sensitive,text", [(True, "I prefer concise replies"),
                                          (False, "My password: abc123")])
def test_sensitive_candidates_are_discarded(sensitive, text):
    from vegapunk.memory_extraction import parse_candidates, sources_from_messages
    assert parse_candidates(json.dumps({"memories": [candidate(text=text, quote=text) | {
        "sensitive": sensitive}]}), sources_from_messages([user_turn(text)])) == []


def test_extractor_runs_real_agent_without_tools_and_closes_provider(monkeypatch):
    from dataclasses import replace
    from vegapunk import memory_extraction
    from vegapunk.backend import create_backend
    from tests.fake_provider import FakeProvider, says
    class ClosingProvider(FakeProvider):
        calls_closed = 0
        request_loop = None

        async def stream(self, request):
            import asyncio
            self.request_loop = asyncio.get_running_loop()
            async for event in super().stream(request):
                yield event

        async def aclose(self):
            import asyncio
            assert asyncio.get_running_loop() is self.request_loop
            self.calls_closed += 1

    fake = ClosingProvider(says(json.dumps({"memories": [candidate()]})))
    cfg = replace(memory_extraction.config, db_file=db.db_path(), memory_timeout=240)
    backend = replace(create_backend("local", cfg), provider=fake)
    seen = []
    def build(provider, cfg):
        seen.append((provider, cfg))
        return backend
    monkeypatch.setattr(memory_extraction, "create_backend", build)
    extractor = memory_extraction.ModelExtractor(cfg)
    try:
        sources = memory_extraction.sources_from_messages([user_turn("I prefer concise replies")])
        assert extractor(sources)[0].content == "Prefers concise replies"
        assert not fake.last_request.tools
        assert seen[0][0] == "local"
        assert seen[0][1].max_output_tokens == 2048
        assert seen[0][1].provider_max_attempts == 1
        assert seen[0][1].provider_turn_timeout == 240
    finally:
        extractor.close()
    assert extractor.agent is None
    assert fake.calls_closed == 1
    extractor.close()
    assert fake.calls_closed == 1


def test_active_memory_id_can_show_its_extraction_evidence():
    from vegapunk import memory_jobs
    save()
    memory_jobs.process_one(extract_one, now=NOW)
    shown = json.loads(memory_jobs.show_candidate(memory.list_memory()[0].id))
    assert shown["quote"] == "I prefer concise replies"
    assert shown["session_slug"] == "chat"
    assert shown["status"] == "active"


def test_live_lease_blocks_other_worker_and_late_lease_owner_cannot_commit():
    from vegapunk import memory_jobs
    save()
    def extract_with_competing_worker(sources):
        assert not memory_jobs.process_one(lambda _: pytest.fail("duplicate call"), now=NOW)
        # A crashed/stalled worker can return after its lease was reclaimed.
        db.execute("UPDATE memory_jobs SET lease_token='replacement'")
        return extract_one(sources)
    memory_jobs.process_one(extract_with_competing_worker, now=NOW)
    assert memory.list_memory() == []
    assert db.query("SELECT lease_token FROM memory_jobs") == [("replacement",)]


def test_lease_outlasts_configured_provider_timeout(monkeypatch):
    from dataclasses import replace
    from vegapunk import memory_jobs
    monkeypatch.setattr(memory_jobs, "config", replace(memory_jobs.config, memory_timeout=240))
    save()
    def extract_with_competition(sources):
        assert not memory_jobs.process_one(lambda _: pytest.fail("lease expired too early"),
                                           now=db.stamp_plus(NOW, 239))
        return extract_one(sources)
    memory_jobs.process_one(extract_with_competition, now=NOW)
    assert len(memory.list_memory()) == 1


def test_foreground_writes_continue_while_background_extraction_waits():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from vegapunk import memory_jobs
    started, release = threading.Event(), threading.Event()
    save()

    def extract(sources):
        started.set()
        assert release.wait(5), "foreground could not write while extraction waited"
        return extract_one(sources)

    def background():
        try:
            return memory_jobs.process_one(extract, now=NOW)
        finally:
            db.close_connection()

    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(background)
        try:
            assert started.wait(2)
            assert "Saved" in memory.save_memory("Uses zsh")
            assert not memory_jobs.process_one(lambda _: pytest.fail("duplicate extraction"), now=NOW)
        finally:
            release.set()
        assert job.result(timeout=2)
    assert {m.content for m in memory.list_memory()} == {"Uses zsh", "Prefers concise replies"}


def test_append_during_extraction_keeps_original_batch_and_queues_remainder():
    from vegapunk import memory_jobs
    save()
    def extract_and_append(sources):
        session_store.save_session("chat", [user_turn("I prefer concise replies"),
                                           assistant_turn("Understood"), user_turn("I use zsh")])
        return extract_one(sources)
    memory_jobs.process_one(extract_and_append, now=NOW)
    assert len(memory.list_memory()) == 1
    seen = []
    memory_jobs.process_one(lambda sources: seen.extend(sources) or [], now=db.stamp_plus(NOW, 120))
    assert [s.text for s in seen] == ["I use zsh"]


def test_failed_candidate_commit_rolls_back_memories_and_checkpoint(monkeypatch):
    from vegapunk import memory_jobs
    save()
    original = memory_jobs._store_candidate
    def fail_after_write(conn, item, slug, stamp):
        original(conn, item, slug, stamp)
        raise RuntimeError("storage interrupted")
    monkeypatch.setattr(memory_jobs, "_store_candidate", fail_after_write)
    memory_jobs.process_one(extract_one, now=NOW)
    assert memory.list_memory() == []
    assert memory_jobs.candidates("active") == []
    assert db.query("SELECT cursor,status FROM memory_jobs") == [(0, "pending")]


@pytest.mark.parametrize("reason", ["max_tokens", "tool_use"])
def test_incomplete_model_output_cannot_produce_a_memory(monkeypatch, reason):
    from dataclasses import replace
    from vegapunk import memory_extraction
    from tests.fake_provider import backend_for, says
    backend = backend_for(says(json.dumps({"memories": [candidate()]}), stop_reason=reason))
    monkeypatch.setattr(memory_extraction, "create_backend", lambda *args: backend)
    extractor = memory_extraction.ModelExtractor(replace(memory_extraction.config, db_file=db.db_path()))
    try:
        with pytest.raises(ValueError, match="finish normally"):
            extractor(memory_extraction.sources_from_messages([user_turn("I prefer concise replies")]))
    finally:
        extractor.close()


def test_job_batches_are_bounded_and_eventually_process_the_tail():
    from vegapunk import memory_jobs
    text = "a" * 25000 + "tail"
    save(text)
    seen = []
    def extract(sources):
        assert sum(len(s.text) for s in sources) <= 12000
        seen.extend(s.text for s in sources)
        return []
    for offset in range(5):
        memory_jobs.process_one(extract, now=db.stamp_plus(NOW, offset * 120))
    assert "".join(seen) == text


def test_legacy_session_is_reported_instead_of_silently_marked_complete():
    from vegapunk import memory_jobs
    save()
    db.execute("UPDATE sessions SET messages=?", (json.dumps([{"role": "user", "content": "old chat"}]),))
    memory_jobs.process_one(lambda _: pytest.fail("unsupported format"), now=NOW)
    assert db.query("SELECT status,attempts FROM memory_jobs") == [("pending", 1)]
