"""Slash commands present human-readable state without changing tool JSON APIs."""

from types import SimpleNamespace

from vegapunk import artifacts, db, execution, progress, session_store
from vegapunk.commands import CommandContext, dispatch


def context():
    session_store.save_session('review', [])
    return session_store.session_identity('review'), CommandContext(session=None, current_name='review')


def test_plan_shows_steps_dependencies_outcomes_and_evidence():
    sid, ctx = context()
    plan = progress.create(sid, 'Inspect service', ['Read logs', 'Review findings'])
    progress.update(sid, plan['id'], 0, 'in_progress', 1)
    progress.update(sid, plan['id'], 0, 'completed', 2, outcome='Logs checked', evidence=['artifact-proof'])
    out = dispatch('/plan', ctx).output
    assert 'Plan: Inspect service' in out and '1. [completed] Read logs' in out
    assert '2. [pending] Review findings' in out and 'Depends on: 1' in out
    assert 'Outcome: Logs checked' in out and 'artifact-proof' in out
    assert plan['id'] in out and 'Revision: 3' in out
    assert '"steps":' not in out and '"operations":' not in out


def test_artifact_read_keeps_text_and_gives_exact_next_command(monkeypatch):
    sid, ctx = context()
    monkeypatch.setattr(artifacts, 'READ_CHARS', 6)
    saved = artifacts.store_text(sid, 'Unicode evidence', 'é\n中\nlast', source_complete=False)
    out = dispatch(f"/artifacts read {saved['id']}", ctx).output
    assert 'Artifact: Unicode evidence' in out and 'Capture: incomplete' in out
    assert 'é\n中\nla' in out and '\\n' not in out
    assert f"/artifacts read {saved['id']} 6" in out
    assert '"artifact":' not in out


def test_artifact_list_and_search_have_navigation_and_empty_states():
    sid, ctx = context()
    assert dispatch('/artifacts', ctx).output == '(no session artifacts)'
    saved = artifacts.store_text(sid, 'Service log', 'first\nunique marker\nlast')
    listing = dispatch('/artifacts list', ctx).output
    assert 'Service log' in listing and saved['id'] in listing
    assert '/artifacts read <id>' in listing
    found = dispatch(f"/artifacts search {saved['id']} marker", ctx).output
    assert 'Line 2' in found and 'cursor 13' in found and 'unique marker' in found
    assert '"excerpt":' not in found
    assert dispatch(f"/artifacts search {saved['id']} absent", ctx).output == '(no matches)'


def test_checkpoint_list_is_compact_and_show_preserves_snapshot_evidence():
    sid, ctx = context()
    progress.create(sid, 'Inspect', ['Read'])
    run = execution.start_run(sid, 'Inspect safely')
    latest = progress.checkpoints(sid)[0]
    saved = artifacts.store_text(sid, 'Result', 'no mutation observed')
    artifacts.link_checkpoint(sid, saved['id'], latest['id'])
    listing = dispatch('/checkpoints', ctx).output
    assert latest['id'] in listing and 'turn_started' in listing
    assert '/checkpoints show <id>' in listing and '"execution":' not in listing
    detail = dispatch(f"/checkpoints show {latest['id']}", ctx).output
    assert 'Checkpoint:' in detail and run in detail and saved['id'] in detail
    assert 'Inspect' in detail and 'running' in detail


def test_context_report_labels_unknown_usage_and_actor_breakdown():
    report = {'mode': 'observe', 'retained_estimated_tokens': 12000, 'proposed_estimated_tokens': 4000,
              'estimated_input_budget': 8000, 'estimated_budget_exceeded': False,
              'usage': {'scope': 'current conversation', 'coverage': 'completed calls only',
                        'session': {'total_input_tokens': None, 'calls_without_usage': 1,
                                    'by_actor': {'main': {'reported_output_tokens': 12}, 'summary': {'completed_model_calls': 0}}}}}
    ctx = CommandContext(session=SimpleNamespace(context_report=report))
    out = dispatch('/context', ctx).output
    assert 'Context policy: observe' in out and '12,000' in out and '4,000' in out
    assert 'full history' in out and 'Total input tokens: unknown' in out
    assert 'Main' in out and 'Summary' in out and 'Calls without usage: 1' in out
    assert '"usage":' not in out and 'null' not in out


def test_runs_show_observation_status_arguments_and_retrievable_result():
    sid, ctx = context()
    run = execution.start_run(sid, 'Read logs', staged_input='Expanded instructions\nsecond line')
    db.execute("INSERT INTO tool_observations VALUES (?,?,?,'call','read_file',?,'outcome_unknown',NULL,'now','now')",
               ('observation', sid, 'runtime', '{"path":"log.txt"}'))
    out = dispatch('/runs', ctx).output
    assert run in out and 'Read logs' in out and 'Staged model input:' in out
    assert 'read_file' in out and 'outcome_unknown' in out and 'Path: log.txt' in out
    assert 'reconcile' in out.lower() and '"arguments":' not in out


def test_file_reference_change_warning_is_visible(tmp_path, monkeypatch):
    from dataclasses import replace
    from vegapunk import config
    sid, ctx = context()
    monkeypatch.setattr('vegapunk.tools.workspace.config', replace(config.config, workspace_root=tmp_path))
    path = tmp_path / 'note'; path.write_text('before')
    saved = artifacts.register_file(sid, 'Mutable note', 'note')
    path.write_text('after')
    out = dispatch(f"/artifacts read {saved['id']}", ctx).output
    assert 'changed since registration' in out and 'after' in out and 'file reference' in out


def test_artifact_list_pagination_and_plan_empty_history():
    sid, ctx = context()
    assert dispatch('/plan history', ctx).output == '(no plans on this page)'
    ids = [artifacts.store_text(sid, f'Record {i}', 'text')['id'] for i in range(21)]
    first = dispatch('/artifacts list', ctx).output
    second = dispatch('/artifacts list 20', ctx).output
    assert sum(item in first for item in ids) == 20
    assert sum(item in second for item in ids) == 1
    assert 'Next page: /artifacts list 20' in first and 'Next page:' not in second


def test_context_zero_is_distinct_from_unknown_and_errors_stay_visible():
    report = {'mode': 'bounded', 'estimated_budget_exceeded': True,
              'summary_error': 'SummaryValidationError', 'rejected_summary_artifact': 'rejected-proof',
              'usage': {'session': {'total_input_tokens': 0, 'reported_output_tokens': None,
                                   'calls_without_usage': 1, 'summary_cache_hits': 0}}}
    ctx = CommandContext(session=SimpleNamespace(context_report=report))
    out = dispatch('/context show', ctx).output
    assert 'Total input tokens: 0' in out and 'Reported output tokens: unknown' in out
    assert 'Summary error: SummaryValidationError' in out and 'rejected-proof' in out
    assert 'Estimated budget exceeded: yes' in out
    assert dispatch('/context bad', ctx).output == 'Usage: /context [show]'


def test_checkpoint_page_navigation_and_unknown_outcome_warning():
    sid, ctx = context()
    for i in range(26):
        run = execution.start_run(sid, f'Request {i}')
        execution.finish_run(run, 'completed')
    out = dispatch('/checkpoints list', ctx).output
    assert out.count('Checkpoint:') == 50
    assert 'Check next page: /checkpoints list 50' in out
    assert dispatch('/checkpoints list 50', ctx).output.count('Checkpoint:') == 2
    db.execute("INSERT INTO tool_observations VALUES (?,?,?,'call','read_file','{}','outcome_unknown',NULL,'now','now')",
               ('observation', sid, 'runtime'))
    run = execution.start_run(sid, 'Read')
    cp = next(row for row in progress.checkpoints(sid) if row.get('execution', {}).get('run_id') == run)
    out = dispatch(f"/checkpoints show {cp['id']}", ctx).output
    assert 'outcome_unknown' in out and 'reconcile before repeating' in out


def test_terminal_controls_are_escaped_in_bodies_titles_matches_and_state():
    sid, ctx = context()
    unsafe = '\x1b[2J\x1b]52;c;payload\x07\r\x9b'
    saved = artifacts.store_text(sid, 'Title ' + unsafe, 'é\n中\tmarker ' + unsafe)
    progress.create(sid, 'Plan ' + unsafe, ['Inspect'])
    for command in ('/plan', '/plan history', '/artifacts list',
                    f"/artifacts read {saved['id']}", f"/artifacts search {saved['id']} marker",
                    '/checkpoints'):
        out = dispatch(command, ctx).output
        assert all(c not in out for c in ('\x1b', '\x07', '\r', '\x9b'))
        assert '\\x1b' in out
    out = dispatch(f"/artifacts read {saved['id']}", ctx).output
    assert 'é\n中\tmarker' in out
    assert artifacts.read(sid, saved['id'])['text'].endswith(unsafe)


def test_file_reference_is_not_labelled_as_an_incomplete_snapshot(tmp_path, monkeypatch):
    from dataclasses import replace
    from vegapunk.tools import workspace
    sid, ctx = context()
    monkeypatch.setattr(workspace, 'config', replace(workspace.config, workspace_root=tmp_path))
    (tmp_path / 'note').write_text('abc')
    saved = artifacts.register_file(sid, 'Reference', 'note')
    for command in ('/artifacts list', f"/artifacts read {saved['id']}"):
        out = dispatch(command, ctx).output
        assert 'incomplete' not in out and '0/3 bytes' not in out
        assert '3 bytes' in out
    assert 'unchanged since registration' in dispatch(f"/artifacts read {saved['id']}", ctx).output


def test_memory_show_formats_fact_and_source_evidence():
    from vegapunk import memory_jobs
    _, ctx = context()
    db.execute('INSERT INTO memory_candidates (id,fingerprint,category,content,topic,confidence,source_id,quote,session_slug,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
               ('abc123', 'fingerprint', 'preference', 'Prefers concise answers', 'style', 0.9, 'source-1', 'Please be concise\nwhen answering', 'review', 'pending', db.utcnow()))
    out = dispatch('/memory show abc123', ctx).output
    assert 'Memory candidate: abc123' in out and 'Status: pending' in out
    assert 'Fact:\nPrefers concise answers' in out and 'Source session: review' in out
    assert 'Source quote:\nPlease be concise\nwhen answering' in out
    assert 'Model confidence: 0.90' in out and '"content":' not in out
    assert memory_jobs.show_candidate('not-an-id').startswith('Usage:')


def test_explicit_memory_show_preserves_unicode_but_escapes_terminal_controls():
    from vegapunk import memory
    _, ctx = context()
    memory.save_memory('Prefers é\nconcise\x1b[2J replies')
    item = memory.list_memory()[0]
    out = dispatch(f'/memory show {item.id}', ctx).output
    assert 'Explicitly saved memory (no extraction evidence):\n' in out
    assert 'Prefers é\nconcise\\x1b[2J replies' in out and '\x1b' not in out
