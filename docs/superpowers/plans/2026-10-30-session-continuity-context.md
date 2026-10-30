# Session continuity and context efficiency implementation plan

> **For agentic workers:** Implement this plan task by task using the host's normal execution workflow. If available, use `superpowers:executing-plans`; use subagent-driven development only when authorized by the host. Check off completed steps, verify each deliverable, and open separate reviewable PRs as specified below.

**Goal:** Give Vegapunk durable session plans, deterministic progress checkpoints, retrievable artifacts, and bounded model context while preserving evidence and response quality.

**Architecture:** Vegapunk owns application state, persistence, tools, retention, and context-selection policy. Logpose owns the provider-neutral execution loop and an optional request-preparation extension. Retained history, selected model context, session artifacts, execution observations, and personal memory are distinct data sets.

**Tech stack:** Existing Python, Logpose, embedded Turso via `vegapunk/db.py`, typed `@tool` functions, prompt_toolkit, Rich, pytest. Do not add a service, vector database, workflow engine, or dependency for the first implementation.

**Spec:** This document contains the implementation specification and task sequence. It supersedes the informal chat proposal for this feature. Existing unrelated installer/prompt drafts are outside scope.

## 1. Starting point and verification scope

Inspected 2026-10-30. Vegapunk HEAD: `de027e54e550ae49cc89856bf2097f8c211e74e6`; current feature branch: `feat/session-plan-checkpoints`, with no implementation yet. Reinspect before implementing; do not overwrite unrelated untracked files.

Current code paths:

| Location | Current responsibility and gap |
|---|---|
| `vegapunk/session.py` | Keeps a Logpose Conversation; `send()` rolls partial history back on interruption. No persisted plan or active session identity. |
| `vegapunk/cli.py` | Refreshes system prompt before user turns; autosaves/names sessions after completion. First-turn tools currently precede creation of a saved session. |
| `vegapunk/session_store.py` | Persists message JSON keyed by mutable slug; rename/delete handle memory extraction ownership. |
| `vegapunk/db.py` | Shared embedded database, schema version 15, serialized transactions/migrations. Preserve existing migration and backup behavior. |
| `vegapunk/tools/registry.py`, `tools/__init__.py` | Typed registration and stable tool ordering; universal capability registry. |
| `vegapunk/tool_runtime.py` | Joins synchronous handlers through cancellation; do not introduce wrappers that detach active tool effects. |
| `vegapunk/delegation.py` | Fresh conversation/backend per child, shared approval policy, no nested delegation; child tool results and prompt overhead need accounting. |
| `vegapunk/commands.py`, `prompter.py` | Slash commands plus local argument completion. New commands must appear in both. |
| `vegapunk/memory.py`, `prompt.py` | Every saved fact is injected into the main system prompt; bounded recall exists separately. |
| `vegapunk/skills.py` | Already advertises short descriptions and loads bodies on demand; preserve this design. |
| `vegapunk/tools/filesystem.py` | Full-file reads and directory listings are unbounded. Other tools clip output at a character cap. |
| `vegapunk/runtime.py` | Existing metadata logging includes usage/cache counts and result bytes; no component context budget report. |

`requirements.txt` pins Logpose to `832994c47ce541085661a6b9af46f9fdab79836d`. The separate local Logpose checkout is at `f543e7b3930c8a47e4706a08d38626c36c608601`; do not assume it is the dependency source or base for a PR. Both inspected request builders send `messages=list(history)` and all tool schemas without a context-policy hook. Check the installed dependency's `direct_url.json`, upstream master, and provider tests before modifying Logpose.

This is a code-based design, not a measured performance result. No production transcripts, credentials, or memory contents were inspected for this plan.

## 2. Ownership and hard constraints

| Concern | Owner |
|---|---|
| Session IDs, plans, checkpoints, artifacts, retrieval tools, retention | Vegapunk |
| When and what to summarize/retrieve; memory relevance; budgets | Vegapunk |
| CLI status, approvals, commands, agent roles | Vegapunk |
| Model/tool loop, retries, tool invocation, provider conversion | Logpose |
| Generic optional preparation of the next model request | Logpose |
| App database paths, artifact IDs, personal memory semantics | Never Logpose |

Global constraints:

- No scheduler expansion, recurring jobs, Moltbook integration, new permission profiles, or global installer work.
- Preserve shared auto/manual approvals, synchronous cleanup, Ctrl-C behavior, and sequential chat input.
- Checkpoints describe observed execution. They do not provide exactly-once external actions or automatic workflow replay.
- Model-authored completion is an assertion linked to evidence, not independent proof of correctness.
- Never rerun an uncertain shell/network/write action merely because it lacks a completion checkpoint.
- Persist application state transactionally. Do not silently fall back to ephemeral state after a write failure.
- Never promote tool output, checkpoints, or generated summaries into personal memory automatically.
- Preserve canonical transcript and provider-specific opaque blocks. Select context on copies; do not rewrite stored messages to save tokens.
- Scope retrieval to the owning session. This is ordinary data correctness, not a new approval system.
- No secrets or raw artifacts in diagnostic logs. Existing tool capability/approval rules still apply.
- Public additions use type hints, deterministic tests, and project registry conventions.

## 3. Concrete behavior

### Plans and progress

Use one active plan per session; retain finished/superseded plans. A plan has a stable ID and monotonic revision. Steps have stable IDs, ordered positions, dependencies, status, and linked evidence.

Statuses: `pending`, `in_progress`, `completed`, `blocked`, `cancelled`. Allow multiple `in_progress` steps only when dependency checks pass; parallel sub-agents require this. Dependencies must point within the same plan and form an acyclic graph.

Allowed transitions: pending -> in_progress/cancelled; in_progress -> completed/blocked/cancelled; blocked -> in_progress/cancelled. A completed/cancelled step can return to pending only through an explicit reopen operation with a reason. Starting/completing a step requires completed dependencies. Completing requires a nonempty outcome and at least one evidence reference. Evidence may be a tool observation, immutable artifact, or canonical message reference; the UI labels who asserted completion.

Reject reopening an ancestor or changing dependencies when any transitive descendant is in_progress/completed/blocked. Require explicit leaf-to-root reopening/cancellation with reasons first. Dependency edits apply only to pending steps. Validate the entire resulting graph atomically; never leave a completed descendant dependent on unfinished work.

Every mutation takes `expected_revision`. In one immediate transaction, verify revision, validate input, apply mutation, increment revision, append an event/checkpoint, and return the new state. Concurrent stale writers receive a conflict with the current revision and must reread. Idempotency keys deduplicate retries of the same mutation; they do not authorize replay of actions.

Plan creation/revision is an LLM decision guided by the prompt. Persistence, state transitions, dependency checks, and lifecycle checkpoints are deterministic. Do not advertise automatic decomposition or objective verification.

### Checkpoints and recovery

Record checkpoints automatically at: turn start, tool intent, tool outcome, plan mutation, turn end, interruption/failure, and recovery of an abandoned run. A checkpoint includes a compact plan snapshot/revision, latest committed observation sequence, reason, and optional next-action note. Keep the most recent checkpoint summary readily accessible; paginate older checkpoints.

At turn start, before inference, persist a stable request_id, the original user request, and any staged-skill input separately in the execution record. Transcript rollback cannot erase this recovery input. Resume shows the original request and what was observed without inserting an invented assistant/tool exchange into provider history.

Persist tool intent before executing the handler. If intent persistence fails, do not execute the action. Persist result/error after execution, preserving original call ID and status. If outcome persistence fails, surface an explicit persistence error; never retry the handler automatically. On restart, unmatched intents are `outcome_unknown`; inspect current external state before proceeding. Turn rollback must not delete records of tools that actually ran.

Plan/observation records survive interruption independently of transcript rollback. Resume context must explain this difference and include the latest observation/checkpoint references. Do not manufacture provider messages for partially recorded tool calls. Before beginning an externally consequential next step, the model must reconcile unknown outcomes through read-only tools or user clarification when inspection cannot establish state.

### Artifacts

Store immutable text/JSON snapshots and explicit mutable-file references. An artifact ID resolves to metadata, preview, and retrievable content; it is not an instruction. Reference scope comes from the running session, not model-supplied session names.

For v1, keep text and JSON bodies in the existing database. Stream large shell results into bounded chunks (64 KiB UTF-8 chunks, max 16 MiB captured per invocation by default); record observed/captured byte counts, digest of captured bytes, and `capture_complete=false` when limited. Never imply a partial capture is complete. Binary bodies and remote object storage are deferred; support metadata references to existing approved workspace files.

File references include normalized workspace-relative path and hash/size when registered. Reading a changed file reports that it changed; deleted/missing files report unavailable. A snapshot is required when historical contents matter. Registering a reference never grants file access beyond current workspace rules.

Artifacts and events remain available until explicit session deletion. No automatic age-based deletion in v1. Show stored bytes in `/artifacts`; require an explicit cleanup action if storage becomes excessive. Deleting a session removes its internal snapshots/state in the same transaction, but never deletes external workspace files. Backup/restore must include all artifact rows/chunks.

## 4. Storage model

Use migrations through `db.py`; schema version numbers below are intentionally not preassigned because other PRs may land. Add indexes and constraints, and keep all existing session/memory data.

| Table/addition | Fields and invariants |
|---|---|
| `sessions.session_id` | UUID string, unique and immutable; backfill existing rows transactionally. Slug remains display/lookup key. |
| `execution_runs` | run_id PK, session_id, parent_run_id nullable, actor, started_at, finished_at nullable, status (`running/completed/interrupted/failed/abandoned`), next_sequence. |
| `execution_events` | event_id PK, session_id, run_id, sequence, event_type, tool_call_id nullable, payload_json, created_at. Unique(run_id, sequence); unique intent/outcome keys per tool call where appropriate. |
| `plans` | plan_id PK, session_id, title, status (`active/completed/superseded/cancelled`), revision, created_at, updated_at. At most one active via transaction/index. |
| `plan_steps` | step_id PK, plan_id, position, title, status, outcome, evidence_json, assigned_actor nullable. Unique(plan_id,position). |
| `plan_dependencies` | plan_id, step_id, dependency_id; all resolve within plan. |
| `plan_mutations` | plan_id, operation_id, resulting_revision; unique(plan_id,operation_id) for retry deduplication. |
| `checkpoints` | checkpoint_id PK, session_id, run_id nullable, event_sequence nullable, plan_id nullable, plan_revision nullable, reason, snapshot_json, next_action, created_at. |
| `artifacts` | artifact_id PK, session_id, run_id nullable, tool_call_id nullable, kind (`tool_output/text/json/file_reference`), title, mime_type, size_bytes, captured_bytes, sha256 nullable, capture_complete, file_path nullable, preview, created_at. |
| `artifact_chunks` | artifact_id, chunk_index, body TEXT; ordered reconstruction; unique(artifact_id,chunk_index). |
| `checkpoint_artifacts` | checkpoint_id, artifact_id; validate same session. |
| `context_summaries` | summary_id, session_id, source_digest, covered_message_ids_json, content_json, created_at, model, policy_version. Only reusable when source digest matches. |

Use explicit child-row cleanup in repository transactions unless foreign-key cascades are confirmed by real Turso tests. IDs are UUIDs; do not use mutable slug or list index as identity. Existing messages need a parallel persisted ID mapping: add `session_message_ids` (session_id, ordinal, message_id, content_hash), preserving the longest unchanged prefix at save and assigning new IDs to changed/appended records. References include hash so a mismatched/corrupt source is rejected, not silently rebound. Do not add unknown fields to Logpose provider message payloads.

## 5. Interfaces and file map

These are proposed public interfaces. Use dataclasses/typed models in each owning module; avoid a generic repository framework.

```python
# vegapunk/execution.py
@dataclass(frozen=True)
class ExecutionScope:
    session_id: str
    run_id: str
    actor: str
    parent_run_id: str | None = None
    assigned_plan_id: str | None = None
    assigned_step_id: str | None = None

# vegapunk/progress.py
def create_plan(session_id: str, title: str, steps: list[StepInput],
                operation_id: str) -> Plan: ...
def get_plan(session_id: str, plan_id: str | None = None) -> Plan | None: ...
def update_step(session_id: str, plan_id: str, step_id: str, status: str,
                expected_revision: int, operation_id: str, outcome: str = '',
                evidence: list[str] | None = None, reason: str = '') -> Plan: ...
def revise_plan(session_id: str, plan_id: str, expected_revision: int,
                operation_id: str, changes: PlanChanges, reason: str) -> Plan: ...
def checkpoint(scope: ExecutionScope, reason: str, next_action: str = '',
               artifact_ids: list[str] | None = None) -> Checkpoint: ...

# vegapunk/artifacts.py
def store_text(scope: ExecutionScope, title: str, content: str,
               tool_call_id: str | None = None, kind: str = 'text',
               capture_complete: bool = True) -> Artifact: ...
def register_file(scope: ExecutionScope, path: str, title: str) -> Artifact: ...
def read_artifact(session_id: str, artifact_id: str, start_line: int = 1,
                  max_lines: int = 100) -> ArtifactPage: ...
def search_artifact(session_id: str, artifact_id: str, query: str,
                    max_matches: int = 20) -> ArtifactPage: ...

# vegapunk/context.py
async def prepare_context(request: ContextInput, scope: ExecutionScope,
                          policy: ContextPolicyConfig) -> PreparedContext: ...
```

`StepInput`: title, dependency positions (zero-based input positions resolved to IDs); `PlanChanges`: add steps, change titles/dependencies of unfinished steps, reopen steps, or set plan lifecycle status. Preserve historical events. `Plan`: ID, session ID, revision, status, ordered steps. `ArtifactPage`: metadata, numbered text, next_line nullable, clipped flag. `Checkpoint`: ID, snapshot, reason, next_action. Parse/validate boundary models with the existing Logpose schema facilities where supported; use JSON-string arguments only if nested typed models are not supported by the pinned version, and test/document that compatibility decision before tool registration.

New model tools in `tools/progress.py`: `create_plan`, `get_plan`, `update_plan_step`, `revise_plan`, `save_checkpoint`. New tools in `tools/artifacts.py`: `list_artifacts`, `read_artifact`, `search_artifact`, `register_artifact` (snapshot text/JSON or reference an existing workspace file). Use exact names in schemas/docs/tests. Internal progress metadata writes need no human approval, matching internal memory bookkeeping; reading artifacts is read-only. Existing shell/file/network actions retain their approval boundaries. No arbitrary artifact-body deletion/write tool in v1.

Bind these tools to `ExecutionScope` through per-session/per-run ToolDef handler closures. Do not accept session_id from model arguments and do not add a mutable process-global current-session variable. Preserve ToolSpec name/schema/guard status while replacing handlers. `RoleRunner` obtains child scopes from the parent's run context and binds child tools separately, sharing session_id but using distinct run_id/actor. Any ContextVar use must be installed inside the actual provider loop and explicitly passed when creating child event-loop threads; test this propagation. Standalone callers without a persistent scope receive a clear unavailable result for progress/artifacts; unrelated tools keep working.

Extend `delegate(role: str, task: str, plan_id: str = '', step_id: str = '') -> str` and the runner protocol with the same optional arguments. Both IDs must be present together, resolve in the current session, and identify an active assigned step; validate before spawning. Set assigned_plan_id/assigned_step_id on child scope. Existing two-argument calls stay compatible and allow evidence reporting without step mutation. Parent retains ownership of structural plan revisions.

CLI: `/plan [show|history]`, `/checkpoints [list|show <id>]`, `/artifacts [list|show <id>|read <id> [start_line]|search <id> <query>]`, `/context [show]`. All default to the current session. Local completion offers subcommands and current-session IDs only. Read-only command handlers call the same repositories as model tools. Keep terminal display compact: `Plan: 2/5 completed · step-name`; no chat queue redesign.

## 6. Context preparation contract (Logpose PR)

Add `src/logpose/context.py`, export the types through `src/logpose/__init__.py`, and wire `Agent(..., context_policy=None)` through a new awaited `_prepare_request(history)` stage at the async provider-loop callsite (currently `agent.py:739`). Keep `_request()` as pure request construction from prepared data. Default behavior is byte/structure-equivalent to current full-history requests.

```python
@dataclass(frozen=True)
class ContextInput:
    messages: tuple[Message, ...]
    system: str
    tools: tuple[ToolSpec, ...]
    model: str
    max_output_tokens: int

@dataclass(frozen=True)
class PreparedContext:
    messages: tuple[Message, ...]
    system: str
    diagnostics: Mapping[str, int | str | bool]

# callbacks may be pure synchronous functions or async application policies
ContextPolicy = Callable[[ContextInput], PreparedContext | Awaitable[PreparedContext]]
```

Await application preparation before each provider iteration, including within one long user turn. Vegapunk loads committed state through `asyncio.to_thread`, performs deterministic selection, and generates a bounded summary asynchronously only when needed. Pure synchronous policies must not perform blocking I/O. The callback receives detached copies: frozen containers alone do not protect mutable Message objects. Validate that tools/model/output limit remain caller-controlled and opaque reasoning/raw blocks are untouched. Preparation failures raise a typed ContextPreparationError; never silently drop history or retry side effects. Cancellation must reach summary inference and drain any started synchronous state writes under the existing join policy. Do not change Conversation or RunResult.messages semantics.

Add a generic tool lifecycle observer only if the existing Logpose event/hook contract cannot provide call IDs before handler execution and definitive completion afterward. Inspect `_invoke`, gates, RuntimeContext and safe-error conversion first. Current metadata runtime observers swallow observer exceptions, so they cannot enforce durable intent persistence. Proposed optional `tool_observer(event: ToolObservation) -> Awaitable[None]`, with `phase=intent|outcome`, call ID, parent run identity supplied by the application, normalized content/status; invoked only for actual handlers (after approval). Observer failure aborts before handler or surfaces a distinct post-effect persistence failure afterward. Defaults no-op. Logpose never imports Vegapunk or stores artifacts. This API is a dependency gate: settle it in the Logpose task before wiring event capture.

## 7. Selection policy and defaults

Treat defaults as initial engineering choices, not industry-proven constants. Expose them through Config and document exact units.

| Setting | Initial default |
|---|---|
| `VEGAPUNK_CONTEXT_POLICY` | `observe`, choices off/observe/bounded; enable bounded only after Task 10 gates |
| `VEGAPUNK_CONTEXT_INPUT_BUDGET` | 32768 estimated tokens, additionally capped by known model window minus output reserve and 2048 safety margin |
| `VEGAPUNK_CONTEXT_RECENT_TURNS` | 4 complete user exchanges (soft preference, never exceed hard budget silently) |
| `VEGAPUNK_MEMORY_CONTEXT_BUDGET` | 1200 estimated tokens |
| `VEGAPUNK_TOOL_INLINE_CHARS` | 4000 chars per large output preview |
| `VEGAPUNK_ARTIFACT_CAPTURE_BYTES` | 16777216 bytes per invocation |
| `VEGAPUNK_ARTIFACT_READ_CHARS` | 12000 chars maximum per retrieval response |
| `VEGAPUNK_SUMMARY_MAX_OUTPUT_TOKENS` | 2048 |

Estimate deterministically from UTF-8 bytes with a clearly named conservative heuristic; this is not a tokenizer or hard provider guarantee. Use native token counting only where already available. Provider-reported usage remains authoritative; include estimator error in reports. If required content exceeds budget, raise a clear context-budget error with `/context` guidance rather than silently dropping instructions. Unknown model windows use the explicit configured input budget and label window unknown.

Selection order:
1. Preserve system instructions, latest user request, explicit current restrictions/corrections, and protocol-required blocks. If classification is uncertain, retain the complete user message rather than infer away a restriction.
2. Include bounded current plan/latest checkpoint and unknown action outcomes as data, not instructions. Refresh between tool steps, not just user turns.
3. Replace eligible large tool-result bodies with status + useful head/tail preview + session artifact ID/retrieval guidance. Keep tool call/result IDs and `is_error`; never turn an error into a success preview. Retrieval responses stay available for the current step, then may be referenced on later requests. Do not offload a retrieval result recursively into an endless reference chain.
4. Keep recent complete exchanges while budget permits. Preserve whole tool-call groups and associated provider reasoning/raw blocks; unsupported block types make that exchange ineligible for compaction until tested.
5. Replace older eligible exchanges with a source-linked summary if available, or retain them and report an explicit budget error. Do not silently omit potentially relevant history when summary generation failed.

Summary fields: goal, explicit user constraints/corrections (with message references), decisions, observed outcomes (event/artifact refs), unresolved questions, and next action. Distinguish assertions from observations. Record source digest, coverage, model, policy version. Refresh only when covered history changes. No summary-of-summary chain as the sole evidence; regenerate from canonical sources. Generate at most once per new compacted prefix, outside the main request path, with its token usage included. Cache invalidation is by content digest, not session title.

Memory: replace full-fact injection with small explicit pinned facts plus task-relevant recall within the 1200-token budget. Add `pinned`, `superseded_by`, and `last_verified_at` metadata through a separate migration; existing facts default unpinned, active, unknown freshness. Pin/supersede changes require explicit user instruction or the existing memory review workflow. Use existing semantic/text recall and exclude superseded facts; text fallback must not load all facts on a miss. Preserve original standing user instructions from chat independently of memory recall.

## 8. Implementation tasks and PR boundaries

For every task: write the named regression tests first; run the narrow command and confirm meaningful failures; implement; rerun; review the diff; commit only intended files with Conventional Commits. Rebase on current upstream base before each PR. Do not bundle Logpose and Vegapunk changes into one repo PR.

### Task 1 / PR A: Stable session identity and durable run records

**Files:** modify `db.py`, `session_store.py`, `session.py`, `cli.py`, `commands.py`; create `execution.py`; tests `test_session_store.py`, `test_db.py`, `test_execution.py`, `test_cli.py`.

- [ ] Tests: migration preserves existing messages/slugs/memory sources; IDs unique/stable through rename; `/new` creates distinct scope; resume restores ID; deletion removes only owned internal state; immediate concurrent migration succeeds.
- [ ] Add `ensure_session_identity(...) -> str` repository API. Allocate a temporary untitled slug/session row before first tool execution, even when interrupted; first successful title replaces display slug through existing rename logic. List interrupted untitled sessions so they are recoverable. Do not call the LLM to title before running the task.
- [ ] Implement run start/end/recovery and monotonic event append within existing transaction locks. Abandoned active runs are recoverable observations, not jobs to auto-restart.
- [ ] Tests: first-turn interruption leaves recoverable identity and original request even before plan creation or just after a first tool effect; successful autosave retains it; rename collision is atomic; custom/mock Session CLI tests still supported.
- [ ] Run `.venv/bin/python -m pytest -q tests/test_db.py tests/test_session_store.py tests/test_execution.py tests/test_cli.py`; update README identity/resume behavior; commit and PR.

### Task 2 / PR B: Validated plans, checkpoints, and commands

**Files:** create `progress.py`, `tools/progress.py`, `tests/test_progress.py`, `tests/test_progress_tools.py`; modify registry imports, `commands.py`, `prompter.py`, `prompt.py`, `cli.py`.

- [ ] Tests: dependency cycles, unknown/cross-session IDs, invalid transitions, missing evidence, multiple independent active steps, stale revisions, duplicate operation IDs, plan replacement without losing history, reopening ancestors with active/completed descendants, and editing dependencies of completed work. Assert rollback leaves revision/events unchanged on validation failure.
- [ ] Implement interfaces from section 5; one transaction for step update + revision + checkpoint. Idempotent retry returns original resulting revision, rejects reuse with different payload.
- [ ] Bind scoped tools; parent owns plan creation/revision, children may update assigned steps only. Extend delegate tool and Runner protocol with optional plan_id/step_id as specified above; test schema compatibility with existing two-argument calls. This prevents sibling rewrites, without restricting normal tools. Children without assignment can report evidence but cannot modify steps.
- [ ] Add `/plan`, `/checkpoints` and completions. Prompt asks substantial tasks to maintain plan; do not force planning for simple questions. Inject concise current state as data each inference step once context hook exists; before then refresh at user-turn boundaries and via get_plan.
- [ ] Tests for command visibility, completions, resume state, child conflict handling, journal mode (no automatic plan creation), and no extra approval prompts for internal progress writes.
- [ ] Run focused progress/CLI/prompt tests, then full suite. Commit/PR, retaining existing sequential input UX.

### Task 3 / PR C: Session artifacts and bounded retrieval

**Files:** create `artifacts.py`, `tools/artifacts.py`, `tests/test_artifacts.py`; modify `db.py`, session deletion, tool imports, commands/completion, README.

- [ ] Tests: exact Unicode reconstruction across chunks, bounded numbered reads/search, invalid ranges, partial-capture labels, cross-session access denial, renamed session, deleted file, changed-file hashes, path escape/symlink rejection through existing workspace resolver, transaction rollback and backup restore.
- [ ] Implement database snapshots/chunks and file references. Validate ranges (`start_line >= 1`, `1 <= max_lines <= 200`, `1 <= max_matches <= 50`) and enforce read character cap with next-page cursor; split oversized single lines with explicit continuation cursor rather than repeatedly returning the same prefix. Use a typed cursor if line-only parameters cannot represent this; expose that cursor consistently in tool schema/tests.
- [ ] Implement `/artifacts` and ID completion; display kind/size/completeness. Keep IDs stable through rename; cleanup internal artifacts with session only.
- [ ] Run focused tests and a real temporary-database tool invocation/read/reopen cycle (no live model). Commit/PR.

### Task 4 / Logpose PR D: Request preparation and lifecycle contract

**Files:** `src/logpose/context.py`, `agent.py`, `__init__.py`, `runtime.py` if observer metadata needed; existing provider/agent tests plus `tests/test_context.py` and lifecycle tests.

- [ ] Start from current Logpose master, not the stale local checkout. Test default request equivalence and canonical Conversation/RunResult preservation first.
- [ ] Implement section 6 types/hook with copy isolation and typed failure. Ensure every provider iteration awaits preparation, including inside a long user turn; test both sync and async callbacks plus cancellation. `_request()` remains pure construction; `_prepare_request()` awaits application policy before calling it.
- [ ] Test request policy across OpenAI-compatible, Responses and Anthropic with representative raw/reasoning blocks and tool-call groups. Treat wire-format constraints as provider-specific, never delete blocks based only on textual appearance.
- [ ] Resolve observer requirement by testing existing gates/events. If needed implement the optional observer contract with approval-before-intent, intent-before-effect, definitive outcome-after-handler; no observer for denied calls. Include failures, exceptions, cancellation and joined synchronous tool completion.
- [ ] Prove post-effect observer failures do not trigger handler/provider retry that repeats the action. Runtime logs contain metadata only.
- [ ] Run Logpose full tests/checks according to its own AGENTS instructions. Publish PR, wait for reviewed merge; record merged SHA. Vegapunk dependency update must wait for this API to land.

### Task 5 / PR E: Tool capture, checkpoints, and output boundaries

**Files:** `execution.py`, `tool_runtime.py`, `session.py`, `delegation.py`, tools `shell.py`, `filesystem.py`, `grep.py`, `fetch.py`, `search.py`; `requirements.txt`; new `tests/test_execution_capture.py`.

- [ ] Pin reviewed Logpose SHA and reinstall in a clean/test venv; do not rely on unchanged `0.1.0` package version. Verify installed direct URL SHA.
- [ ] Wire intent/outcome observation into scoped storage for parent and children. Store artifact before producing reference. Bind run scope within owning execution loop. Terminal rendering may show normal tool output; model view uses selected preview later.
- [ ] Refactor output-capping tools to capture before preview truncation. Shell uses spool/stream capture with bounded RAM/disk and kill/timeout semantics preserved. Existing outputs already truncated upstream must be labelled partial.
- [ ] Add read_file line range arguments and directory pagination with defaults bounded; retain edit_file's exact matching behavior. Reading large files must not require loading the whole file first. Web pages still may require existing parser allocation; bound response downloads explicitly and label limits.
- [ ] Tests: persistence error before action causes zero handler calls; error after action causes one call + explicit uncertainty; interrupted turn removes transcript tail but preserves observed effects; child scopes never cross sessions; retrieval never executes original command; process death between effect and result yields unknown intent on reopen.
- [ ] Run focused tests and full suite; real terminal cancellation smoke test using temporary files/scripted providers. Commit/PR.

### Task 6 / PR F: Context diagnostics before selection changes

**Files:** `context.py`, `runtime.py`, `config.py`, commands/completions; `tests/test_context_diagnostics.py`.

- [ ] Add observe/off modes and `/context`. Report system/memory/skills/schema/history/result estimates, selected vs retained bytes, parent/child usage, summary/retrieval overhead, actual input/output/cache usage when available. Mark overlaps/heuristic estimates; do not claim an exact additive token breakdown.
- [ ] Tests: logs contain no message/body/secret text; absent provider usage is unknown, not zero; component totals and estimator labels stable; children included without double-counting.
- [ ] Capture a synthetic baseline trace with repeated large outputs and independent delegates. Commit baseline fixtures/report metadata, not user conversations. Commit/PR.

### Task 7 / PR G: Bounded context and source-linked summaries

**Files:** `context.py`, create `context_summary.py`, `session.py`, `interactive_agent.py`, `delegation.py`, `config.py`; tests `test_context_policy.py`, `test_context_summary.py`.

- [ ] Implement ordering/defaults in section 7. Preserve original errors and tool IDs; artifact references only when storage succeeded. Inject active plan/checkpoint snapshot on every request. Unknown blocks retain complete exchanges.
- [ ] Summary generation uses separately owned provider client and bounded call/output budgets in async preparation; close clients on their own loops. On failure retain original history or report budget error. Never conceal summary errors with invented notes.
- [ ] Test old user correction retention, newest contradictory instruction precedence, raw/signature preservation, no dangling tool IDs, summary digest invalidation, retrieval use on next model turn, pending approval barrier, and instruction-like artifact content stays data.
- [ ] Test summary provenance and no summary-of-summary drift. Canonical history remains byte-equivalent after any number of preparations.
- [ ] Leave default observe until evaluation gate passes. Commit/PR.

### Task 8 / PR H: Relevant memory and lean delegate results

**Files:** `memory.py`, `prompt.py`, `memory_jobs.py`/`memory_extraction.py` only where review integration requires it, `delegation.py`, Config; memory/prompt/delegate tests.

- [ ] Add memory metadata/retrieval policy from section 7. Test unrelated/empty retrieval, explicit pin, superseded fact, no embeddings fallback, review-mode preservation, and journal behavior.
- [ ] Delegate final response stays small by instruction and artifact offloading when large; preserve error/cancel status. Count all child tokens. Do not route to a cheaper model or alter user-selected effort without a separate measured decision.
- [ ] Keep tools universally accessible. Only implement tool discovery later if schema share is material and cache/selection evaluations justify the extra complexity.
- [ ] Run focused suites/full regression; commit/PR.

### Task 9 / PR I: Usability and recovery polish

**Files:** CLI/render/progress/commands/completion plus tests.

- [ ] Compact plan progress row and checkpoint IDs; no new background chat input queue. Read-only `/plan`, `/checkpoints`, `/artifacts`, `/context` work without LLM calls.
- [ ] Restart shows unfinished plan and unknown operations; user chooses when to resume through ordinary chat, with no automatic side-effect replay. No recurring scheduler introduced.
- [ ] Test plain and Rich terminals, narrow widths, completion entries/IDs, `/new`, journal switching, rename, delete, and repeated Ctrl-C cleanup. Update README command/tool/config tables together. Commit/PR.

### Task 10 / PR J: Evaluation and enablement

**Files:** create `tests/evals/session_continuity/` fixtures, a small runnable benchmark driver and results template under `docs/evaluations/`; Config default only after passing gates.

- [ ] Deterministic scripted-provider cases: 40-turn history, 1 MiB tool output, repeated read/search/retrieve, changing requirements, restarted first turn, renamed session, concurrent siblings, missing artifact, failed summary, oversized mandatory context, crash-uncertain action.
- [ ] Assert zero cross-session leakage, zero duplicated side effects, zero invalid tool pairs, 100% retention of fixture instructions/corrections, accurate status/evidence linkage, and recovery of every committed checkpoint/artifact.
- [ ] Report actual and estimated tokens separately; total includes summary and all children. For synthetic repeated-output fixture require at least 50% reduction in model-facing output bytes relative to full replay, with all evidence retrieval assertions passing. This is an engineering acceptance target, not a promised production saving.
- [ ] Run paired live evaluations on at least 10 representative user-approved synthetic tasks using the configured Z.ai model plus a second supported provider when credentials are available. Compare correctness rubric, repeated tool calls, end-to-end latency, provider/cache usage, uncertainty handling. No credentials -> mark live gate unverified and retain observe default.
- [ ] Enable bounded default only if no hard correctness regression, average total input tokens improve, and median task-quality score is no worse than baseline. Report sample size and variance; do not infer universal quality from 10 tasks. Keep off/observe rollback settings.
- [ ] Full tests for both repos, real local resume/retrieval/cancel smoke tests, review dependency provenance/README, open final PR. Never describe unverified live-provider support as complete.

## 9. Review focus

These conditions must have the explicit tests assigned above before shipping:
1. First-turn crash before naming/autosave: state remains discoverable, scoped, and recoverable (Tasks 1, 5).
2. Tool changed host state but outcome write failed: no blind reexecution and uncertainty visible (Tasks 4, 5).
3. Parallel children race on one plan or stale revision: atomic conflict, no lost update or cross-session state (Tasks 2, 5).
4. Compaction/retrieval loses a user correction or provider signature: preserve mandatory context or stop explicitly (Tasks 4, 7, 10).
5. Huge single line, Unicode chunk boundaries, changed/missing files: bounded retrieval with truthful completeness and forward progress (Task 3).

## 10. Comparison with published practice

These sources support the direction; they do not validate our thresholds or imply that every vendor feature should be adopted.

| Published approach | Decision for Vegapunk | Tradeoff |
|---|---|---|
| Anthropic describes compaction, structured notes, and isolated sub-agent contexts for long work. [Context engineering, 2025-09-29](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents) | Keep source-linked task state and bounded request context; preserve original evidence. | Lossy summaries need correction/provenance tests. |
| Anthropic shows filtering intermediate data outside model context and loading capabilities when needed. [Code execution with MCP, 2025-11-04](https://www.anthropic.com/research/code-execution-with-mcp) | Store large results, retrieve ranges/search matches, reuse existing shell capability. | References cause extra retrieval calls; previews must remain useful. A new code sandbox/MCP layer is not required. |
| LangGraph distinguishes thread checkpoints from cross-thread application stores. [Persistence, accessed 2026-10-30](https://docs.langchain.com/oss/python/langgraph/persistence) | Session progress/artifacts remain separate from personal memory. | Our checkpoints do not reproduce LangGraph graph replay; don't claim equivalent durable execution. |
| OpenAI documents stable cache prefixes and compaction/cache tradeoffs. [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) | Keep stable instructions/tool order and measure cache reuse alongside total input. | Cache behavior is provider/model-specific; verify Z.ai separately. Caching does not remove context pollution. |

Do not add automatic multi-agent fan-out, tool-discovery infrastructure, external vector stores, or model routing in the initial continuity work. Each is a later optimization gated by component measurements. Preserve existing skill progressive disclosure and bounded incremental memory extraction.

## 11. Handoff and completion report

The implementing model should read this document, both repositories' current AGENTS.md, add-tool/verification/review guidance, and the actual installed Logpose API. Refresh the base revisions; mark deviations and the evidence behind them in the PR. No access to prior chat is required.

For each PR report: concrete delivered behavior, migrations/backward compatibility, tests and smoke runs performed, unverified live-provider behavior, mergeability/CI, and dependency on earlier PRs. Merge Logpose API changes before pinning them from Vegapunk. Preserve unrelated drafts. Do not claim deterministic correctness of LLM decisions; claim only the tested deterministic persistence/validation/recovery behavior.
