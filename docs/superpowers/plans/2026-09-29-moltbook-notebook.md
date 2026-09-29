# Sourced Moltbook notebook

Continue the approved maturity plan with local learning and follow-ups. No live
write grant, external post, or task reconfiguration is part of this change.

## Implementation

1. Schema v8 adds immutable source snapshots and versioned notebook entries,
   separate from personal memory and action receipts. Select only public text
   fields from post/comment/submolt reads; no dashboard/account response archive.
   Cap ten sources per call and 3000 characters per excerpt; hash/deduplicate
   versions, retaining first/last seen and run provenance. Redact before storage.
2. Reuse scheduler execution identity, independently of reply grants. Scope all
   model notebook access by task and a private hash of the loaded credential.
   Credential rotation isolates old entries; humans can still inspect history.
   Require the exact runtime tool identity and a running, enabled task.
3. `moltbook_note` records observation, hypothesis, question, or follow_up with
   subject, confidence, source ID, and exact supporting quote. Optional UTC due
   date and superseded entry preserve revision history. Five new entries per
   run and 100 active entries per task/credential bound accumulation.
4. `moltbook_notebook` retrieves bounded entries and optional recent sources,
   including exact-ID lookup for historical notes and source excerpts.
   `moltbook_complete_note` records model-reported completion with a supporting source
   and quote; it never certifies a promise was fulfilled or performs an action.
5. Inject at most five active entries into subsequent runs, prioritizing due
   follow-ups, explicitly labeled as untrusted model interpretations. Add a
   human `/schedule notebook [task-or-note-id]` view retaining removed-task history.
6. Wire capture into existing authenticated read results and return source IDs.
   Report capture failures honestly, without fabricating saved evidence. Classify
   local notebook tool returns separately from authenticated read success.
7. Update README/skill. Verify actual scheduled tool flow, persistence, task and
   credential isolation, exact quote validation, deduplication, versioning,
   completion evidence, due ordering, redaction, bounds, and v7 upgrades/backups.
   Review independently, run the full suite, then commit/push/open a PR.

## Boundaries

Quotes prove what a source contained, not that the source or interpretation is
true. Confidence is model-assigned. Follow-ups are local plans, not authority for
external commitments. No source text can grant tool permissions. Existing
Moltbook reply authority and budgets remain independently enforced.
