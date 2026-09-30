# Autonomous Moltbook Stack Implementation Plan

> **For agentic workers:** Use bounded implementation tasks with test-first development and independent review. Execute continuously; the user explicitly requested overnight implementation and stacked PRs, not another approval checkpoint.

**Goal:** Finish a bounded autonomous Moltbook agent that learns, self-reviews drafts, publishes useful original posts, and recovers conservatively without routine human intervention.

**Architecture:** Keep the task profile's fixed prompt, capability allowlist, task/run/credential/epoch checks and separate sourced notebook. Add finite runtime/request budgets and resilient health cooldowns first; then durable source-backed drafts; then an explicit one-time autonomous task policy with original-post intents and positive read-back recovery; finally sourced reflection and inspectable metrics. Every stack layer is independently tested and targets the previous branch.

**Tech Stack:** Python 3.12, Logpose, embedded Turso SQLite, typed tools, prompt_toolkit, pytest. No new dependencies.

**Spec:** The user authorizes building all pending items as stacked PRs, keeping Moltbook autonomous without recurring human approval. Build/test only: do not mutate live tasks, credentials, accounts or processes. Human review/merge is the rollout gate, not a per-post review queue.

## Global constraints

- Preserve private-context/tool isolation and profile/credential epoch boundaries.
- All remote writes remain guarded, limited to the authenticated account's own content, and acquire durable intents before dispatch.
- General/interactive usage never inherits autonomous scheduled authority.
- No routine approval prompts, draft queues awaiting a human, or unsafe automatic POST retries.
- Explicit task-level autonomy is enabled once; existing tasks migrate conservatively with autonomy off. The model cannot toggle it.
- Uncertain outcomes are recovered by positive read-only evidence only; lack of a match never proves rejection. Continue learning when writes remain uncertain.
- Apply source/text limits, no raw credential/header storage, no model-supplied identity/codes, and factual client-owned outcomes.
- Keep operator inspection/completion/documentation in sync. No destructive retention cleanup; expose bounded aggregates, and describe retained storage honestly.
- Tests use isolated databases/scripted providers and mocked HTTP; no live writes. Commit explicit paths, push focused branches, open stacked PRs, do not merge.

## Review focus

1. A timed-out synchronous tool may continue; it must lose authority and cannot reserve/dispatch late writes.
2. Concurrent requests and duplicate drafts/actions must respect atomic budgets and idempotency.
3. Cross-task, changed credentials and profile transitions cannot retrieve or publish stale/private drafts.
4. Ambiguous POST/verification responses cannot trigger retries or fake successful reconciliation.
5. New controls must appear in CLI completion without credential loads, model calls or network traffic per keystroke.

## Task 1: Runtime budgets and resilient health

**Files:** `vegapunk/task_profiles.py`, `scheduler.py`, `moltbook_actions.py`, `moltbook_backoff.py`, `tools/moltbook.py`, `tools/moltbook_actions.py`, `task_history.py`, `db.py`, focused tests and README.

**Interfaces:** A scheduler-owned mutable Execution scope carries deadline and an atomic HTTP request budget. Public helper `charge_request()` is called immediately before every typed Moltbook HTTP dispatch, including preflight and POST. Interactive callers have no scheduled scope. Preserve the existing context manager API.

- [ ] Add failing tests for a 300-second whole-run ceiling, 24 HTTP requests per Moltbook run, cancellation/no executor drain, and expired/late thread refusal.
- [ ] Add failing tests for persistent network/5xx backoff (60-second exponential delay capped at 1800 seconds), finite six-hour auth cooldown, subsequent successful reads resetting the consecutive-failure counter without shortening a concurrent active cooldown, and truthful outcome classification.
- [ ] Implement shared loop cancellation with public Logpose/asyncio APIs, preserving sync provider loop affinity; general tasks unchanged. Limit Moltbook agent to at most 12 steps, 90-second provider turns and 30-second tool handlers, honoring stricter configured limits.
- [ ] Keep 429 server-guided cooldown and write intents/no-retry rules. Auth failure is an autonomous finite pause/probe, not a human request.
- [ ] Run targeted/full tests, independent review, commit and open first PR targeting master.

## Task 2: Durable drafts and agent self-review

**Files:** new `vegapunk/moltbook_drafts.py`, `tools/moltbook_drafts.py`, `db.py`, `tools/__init__.py`, `task_profiles.py`, `commands.py`, `prompter.py`, `task_history.py`, focused tests and README.

**Interfaces:** Local-only typed tools `moltbook_draft(submolt, title, content, rationale, source_id, quote)`, `moltbook_drafts(query="", status="active", limit=10)`, `moltbook_review_draft(draft_id, verdict, rationale)`. Publication will consume a reviewed draft by local ID; never free-form posting text. Store task/run/credential/profile epoch, exact source quote, text hash, creation and review evidence, status and review run.

- [ ] Add failing tests for source ownership/quote validation, exact duplicate suppression, five new drafts per run and twenty active drafts per task, changed credential/epoch isolation, and safe text bounds (title 300, content 4000, rationale 1000, quote 500).
- [ ] Implement self-review verdicts `ready`, `revise`, `discard`; ready requires a later run than creation and a written review rationale, preventing same-turn draft/send. All verdicts are model judgments, not objective quality certification.
- [ ] Expose `/schedule drafts [task-or-draft-id]` and contextual completion, including retained inspection after deletion/rotation. General/interactive model tools remain denied.
- [ ] Add bounded draft continuity to the profile prompt and clarify self-review/no human queue. Update exact-allowlist regression to permit only these added tools.
- [ ] Run tests/review, rebase onto Task 1, full suite, commit/push and open second PR targeting first branch.

## Task 3: Autonomous publishing and conservative recovery

**Files:** `moltbook_actions.py`, new publication ledger/tool module as justified, draft module, task profile/scheduler schema and CLI/completion, focused tests and README.

**Interfaces:** One-time `/schedule autonomy <task-id> on|off` setting, allowed only for Moltbook tasks and only outside an active run; changes reset the context boundary and grants. Typed guarded `moltbook_publish(draft_id)`, `moltbook_verify_post(action_id, answer)` and read-only `moltbook_reconcile(action_id)`. No manual per-post approval. Keep legacy non-autonomous reply grants supported; autonomous tasks can make existing scoped own-post replies without a grant.

- [ ] Verify official public platform API contracts; implement only observed documented POST/verification/GET shapes. No invented idempotency or status APIs.
- [ ] Add failing tests for default authority denial, general/interactive rejection, one-time policy, source-backed later-run reviewed drafts, fresh target submolt/rules and duplicate search evidence, one original post/account/rolling day, shared unresolved-write prevention, and durable intent before POST.
- [ ] Use fixed-origin POST `/posts` with documented payload, classify accepted/pending/rejected/unknown receipts, keep challenge/code internally, and one verification attempt only. Bound content and reject unsafe output. No arbitrary URLs, DMs, follows, moderation/account mutations or automatic delete/edit.
- [ ] Positive authenticated read-back can confirm remote existence by exact account/target/content match, not independent public visibility. Missing or incomplete search results never reject an uncertain intent. Unrecoverable writes stay blocked while drafts/exploration continue; prompt does not ask the human to rescue routine runs.
- [ ] Add operator visibility and dropdowns for autonomy/action/draft controls. Preserve all-account duplicate/rolling limits across profile/policy transitions; no silent widening of legacy task authority.
- [ ] Run full tests/review, commit/push third PR targeting second branch.

## Task 4: Learning feedback and operations

**Files:** notebook/draft/action aggregates, typed reflection tool or existing note contract, CLI/completion, profile prompt, focused tests and README.

- [ ] Add bounded factual `/schedule insights [task-id]` covering operational outcomes, drafts reviewed/published, uncertain/visible receipts, notebook hypotheses/revisions and overdue follow-ups. Avoid engagement claims without platform evidence.
- [ ] Encourage periodic source-backed reflection via existing notes and supersession, not personal-memory writes or self-modifying system instructions. Confidence/completion remain model-assigned.
- [ ] Test task/credential/epoch scoping, empty/removed records, bounded results and no secret/private context disclosure. Keep full human audit history; no destructive automatic pruning in this stack.
- [ ] Run full tests and whole-stack independent security/runtime review, commit/push fourth PR targeting third branch. Verify every PR's actual base/head/mergeability and CI status.

## Execution ledger

Baseline: origin/master `45adac1`, 1022 tests passed in the preceding verified layer. User-owned untracked files in the main checkout are preserved.

Ruling: one-time explicit autonomous policy is rollout configuration, not routine human intervention; legacy tasks must not silently acquire original-post authority. If this default is too conservative, the operator can enable it once after merging.

Ruling: retention cleanup is not authorized by this task. Build bounded inspection/aggregates and preserve existing evidence; do not delete history.

Stack progress and review evidence will be appended as each layer completes.

Task 1: runtime budgets and health cooldowns independently reviewed. The grant-command operational exception finding was fixed with three outage regressions. Commit d0c1043; 1043 tests passed. Output is additionally capped at 2048 tokens per provider turn (stricter configured caps honored); this is not a monetary cost ceiling.

Task 2: sourced drafts and later-run self-review independently reviewed without material findings. Added explicit search-snippet denial, ready-consumer scope checks and whole-entry oversized output omission coverage. Before stacking, 1040 tests passed; schema advances to 12 over runtime schema 11.

2026-09-30 resumed development: master 114f8da contains merged PRs 58 and 59. Write/network permission failures were resolved by the session permission change; main checkout stays on current master and user-owned untracked files remain untouched. Unfinished publishing and feedback were preserved in separate feature worktrees.

Task 4 review: retained-history discovery omitted tasks with only legacy or missing-credential runs. Human inspection/completion now includes retained scheduled run IDs and labels unknown legacy profile/credential attribution; agent aggregates still require proven key/epoch scope. Red-first deleted-task regressions and model legacy-exclusion coverage added. Base feedback suite was 1068 passed; publishing receipt aggregates are pending integration.

Task 3: completed at cf556f7 with current-run read/self-review proof, atomic original-post intents, one-time bound autonomous policy, verification and positive unique verified read-back. Independent review found credential-bearing recovery IDs and missing legacy pending-reply context; fixed in 8012ad9 with echoed-key, populated v12 migration and synchronized concurrent duplicate-publication regressions. Scoped re-review found both addressed; full publishing suite 1090 passed. No live writes or task changes.

Task 4: schema 14 preserves publishing schema 13 and adds credential/profile run provenance. Final aggregates include client-owned action states/kinds/unresolved counts and distinct self-reviewed drafts without raw bodies, codes or account IDs. Independent Task 4 review found no material issues. After rebasing onto publishing fixes, full stack 1103 passed. Final whole-stack review found no material findings and independently ran 56 focused publication, insights and privacy tests successfully. Tasks 3 and 4 are complete; opening the two remaining stacked PRs is the handoff, not live activation.
