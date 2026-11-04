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
- #78: automatic snapshots at turn/tool/recovery boundaries,
  exact checkpoint lookup/pagination/completion, linked outcome artifacts, compact
  plan status, resume warnings, and distinct original/staged recovery input.

The stack is #74 → #76 → #77 → #78 → #79 (usage accounting). #75 is independent.
User data, configuration, and unrelated installer/prompt drafts are outside this stack.

## Remaining gates and limitations

- Default stays **observe**. Both Z.ai and Codex completed ten varied source-accuracy
  pairs, including correction, uncertainty, scoped denial, and active-plan retrieval.
  The Codex error was an unsupported configured model for this subscription; an
  evaluation-only catalog-listed override worked. Both strict source gates failed
  on an `ANSWER:` value-format mismatch. Open-ended task quality remains unverified. See
  `../../evaluations/context-efficiency.md` for results, grading limitations, and costs.
- #79 adds a content-free ledger shared by main/delegate/summary
  execution, latest-turn and conversation totals, retrieval counters, cache reuse,
  missing usage, and exact assembled memory/skill byte measurements. Raw completions
  are counted before rendering or summary validation. Ruling: this ledger is scoped
  to the current in-process conversation, clearing on new/restore; existing runtime
  logs retain historical provider metadata. It does not claim complete billing for
  failed/partial attempts that never report usage, or add provider cache counts twice.
- Stable-message-reference branch implements the separate `session_message_ids` map.
  Appends/renames preserve IDs; edits renew the changed suffix. Lookup checks session
  identity and canonical hash. Summary source artifacts attach IDs/hash only when
  their original canonical message matches, and label projected excerpts. Ordinals
  remain artifact-local positions, never persistent cross-edit identity. Schema 23
  backfills valid old transcripts; malformed data is preserved with a warning.
- Memory pin/supersede controls are explicit. `last_verified_at` is reserved metadata;
  no freshness verifier or semantic entailment checker exists. A matching quote
  does not prove a generated note or proposed next action is justified.
- Further open-ended live evaluation remains required before enabling bounded mode
  by default. Actual SIGINT and fresh-process rename/resume smoke tests passed in
  plain and Rich PTYs, retaining completed effects and evidence without replay. An
  abrupt-exit smoke test retained an unknown action correctly but exposed an older
  overlong provisional-name lookup bug; a focused follow-up fix is required.
  Deterministic tests cover the principal failure boundaries,
  including a 40-turn history, a 1 MiB result, retrieval, corrections, and scope denial.

Latest local verification including stable message references: 1,082 Vegapunk tests
pass; Logpose's 950 tests pass. Independent reviews cleared the context policy,
bounded tool payloads, and lifecycle snapshot changes. The bounded-download path
also passed a real local HTTP smoke test. These are local results, not hosted CI.
