# Context implementation delivery status

Updated 2026-11-01. Read alongside the October 30 implementation plan; the original
checkboxes describe the specification, not an up-to-date delivery ledger.

## Delivered or ready for review

- Merged Vegapunk #71–73: stable session identity, run recovery, revisioned plans,
  dependency checks, immutable plan checkpoints, and assigned delegate claims.
- Merged Logpose #18–19: fail-closed tool lifecycle observation and detached
  request-context preparation. Vegapunk pins merged `86deccc` and local dependency
  provenance was verified.
- Vegapunk #74: scoped artifacts, retrieval, file references, tool journals,
  pre-clipping capture, bounded POSIX shell streaming, cancellation cleanup.
- Independent #75: citation and evidence-ranking instructions for main/delegates.
- #76: observe/off/bounded context policy, `/context`, artifact projection,
  source-quoted cached summaries, explicit memory priorities, synthetic evaluator.
- #77: bounded file ranges/cursors, directory pages, streamed capped web downloads,
  artifact-backed large delegate results, and explicit partial/stop status.
- Lifecycle checkpoint branch: automatic snapshots at turn/tool/recovery boundaries,
  exact checkpoint lookup/pagination/completion, linked outcome artifacts, compact
  plan status, resume warnings, and distinct original/staged recovery input.

The stack is #74 → #76 → #77 → lifecycle checkpoint branch. #75 is independent.
User data, configuration, and unrelated installer/prompt drafts are outside this stack.

## Remaining gates and limitations

- Default stays **observe**. A ten-case paired Z.ai lookup experiment is narrower
  than the full task-quality rubric; the second provider returned HTTP 400 before a
  valid pair. Cross-provider enablement is unverified. See
  `../../evaluations/context-efficiency.md` for results, grading limitations, and costs.
- `/context` reports serialized system/schema/history estimates and the latest
  proposal. A complete cumulative accounting of main/delegate/summary calls,
  retrieval overhead, and memory/skill components remains pending. Existing runtime
  logs and delegate activity expose provider usage, but do not constitute that ledger.
- Summary sources use message/block ordinals protected by a canonical-content digest
  and immutable source artifact. The proposed persistent `session_message_ids` map
  is not implemented; do not expose these ordinals as stable cross-edit identifiers.
- Memory pin/supersede controls are explicit. `last_verified_at` is reserved metadata;
  no freshness verifier or semantic entailment checker exists. A matching quote
  does not prove a generated note or proposed next action is justified.
- Further broad live evaluation and real interactive resume/cancel smoke coverage
  remain required before claiming the entire original plan is complete or enabling
  bounded mode by default. Deterministic tests cover the principal failure boundaries,
  including a 40-turn history, a 1 MiB result, retrieval, corrections, and scope denial.

Latest local verification for lifecycle checkpoint delivery: 1,060 Vegapunk tests
pass; Logpose's 950 tests pass. Independent reviews cleared the context policy,
bounded tool payloads, and lifecycle snapshot changes. The bounded-download path
also passed a real local HTTP smoke test. These are local results, not hosted CI.
