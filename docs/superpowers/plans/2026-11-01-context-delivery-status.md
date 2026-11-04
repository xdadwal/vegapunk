# Context implementation delivery status

Updated 2026-11-03. Read alongside the October 30 implementation plan; the original
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
- #79–82: shared usage accounting, varied cross-provider evaluation, stable
  hash-bound message references, and legacy provisional-session resume compatibility.
- #83: full canonical summary evidence, safe complete exchange selection,
  abnormal delegate stop labels, paginated plan history, normalized cache-aware
  input totals, and covered estimator error.
- Final evaluation branch: operational summary/delegate workflow, inert tool-name
  isolation, retained failure/cleanup usage, independent citation dimensions and
  normalized-total gates. Rejected synthetic summaries are retained in reports.

The stack is #74 → #76 → #77 → #78 → #79 (usage accounting) → #80 (evaluation)
→ #81 (message references) → #82 (resume fix) → #83 (final runtime contracts)
→ the final evaluation branch. #75 is independent.
User data, configuration, and unrelated installer/prompt drafts are outside this stack.

## Implementation complete; automatic rollout withheld

- Default stays **observe** by a measured release decision. The final source suite
  passed 10/10 in both modes on Codex; Z.ai passed 10/10 baseline and 9/10 bounded
  under strict parsing. Its failed response contained correct JSON followed by
  prohibited prose. Final hardened operational baselines passed on both providers;
  bounded Z.ai timed out and bounded Codex stopped with a provider error after
  summary generation. No failed pair was replaced by a retry. Earlier passing
  diagnostics and failed summary validations remain documented separately. See
  `../../evaluations/context-efficiency.md` for measurements and limitations.
- #79 adds a content-free ledger shared by main/delegate/summary
  execution, latest-turn and conversation totals, retrieval counters, cache reuse,
  missing usage, and exact assembled memory/skill byte measurements. Raw completions
  are counted before rendering or summary validation. Ruling: this ledger is scoped
  to the current in-process conversation, clearing on new/restore; existing runtime
  logs retain historical provider metadata. It does not claim complete billing for
  failed/partial attempts that never report usage. Logpose normalizes input as uncached;
  #83 adds total input from normalized uncached/read/write categories once. Earlier
  uncached comparisons are not complete context totals or price savings.
- #81 implements the separate `session_message_ids` map.
  Appends/renames preserve IDs; edits renew the changed suffix. Lookup checks session
  identity and canonical hash. Summary source artifacts attach IDs/hash only when
  their original canonical message matches, and label projected excerpts. Ordinals
  remain artifact-local positions, never persistent cross-edit identity. Schema 23
  backfills valid old transcripts; malformed data is preserved with a warning.
- Memory pin/supersede controls are explicit. `last_verified_at` is reserved metadata;
  no freshness verifier or semantic entailment checker exists. A matching quote
  does not prove a generated note or proposed next action is justified.
- Ruling: v1 capture/read/preview hard bounds remain documented constants rather
  than extra environment knobs. Context budget, recency, memory budget, and summary
  controls are configurable. No automatic freshness verifier, semantic truth oracle,
  model routing, tool discovery service, or scheduler expansion is claimed.
- Further open-ended live evaluation remains required before enabling bounded mode
  by default. Actual SIGINT and fresh-process rename/resume smoke tests passed in
  plain and Rich PTYs, retaining completed effects and evidence without replay. An
  abrupt-exit smoke test retained an unknown action correctly and exposed an older
  overlong provisional-name lookup bug. A focused fix canonicalizes new names and
  resolves existing displayed names exactly before normalization. The strengthened
  crash/resume smoke restored the exact session, retained the abandoned request and
  unknown effect, and performed zero inference or action replay.
  Deterministic tests cover the principal failure boundaries,
  including a 40-turn history, a 1 MiB result, retrieval, corrections, and scope denial.

Latest local verification: 1,106 Vegapunk tests pass; an isolated clean checkout of
#83 passes 1,095 tests independently of the evaluation branch. Logpose's 950 tests
were previously verified; its pinned code is unchanged. Independent reviews cleared the context policy,
bounded tool payloads, and lifecycle snapshot changes. The bounded-download path
also passed a real local HTTP smoke test. These are local results, not hosted CI.
