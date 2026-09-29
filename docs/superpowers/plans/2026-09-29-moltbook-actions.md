# Scoped Moltbook replies and receipts

Continue the approved staged-autonomy plan with one opt-in operation: replies to
comments on the authenticated account's own posts. Do not grant any live task.

## Design and implementation

1. Schema v7 adds task permissions, account backoff, and durable reply actions.
   Grants come only from `/schedule grant <task> moltbook.reply_own`, bound to the
   authenticated account. Revoke and inspect commands remain human CLI actions.
2. A scheduler-owned ContextVar identifies task/run across the real logpose sync,
   gate, and thread boundaries. Every action rechecks the running row and grant;
   no model-supplied task/run identifier can confer authority. Guarded tools are
   allowed by the unattended gate only for these explicitly scoped operations.
3. Before POST, verify account and post ownership with authenticated GETs. Require
   a parent comment and bounded content. Commit a unique account/post/parent intent
   under an immediate transaction, with one new reply/run, three/account/rolling
   day, six hours/thread, a 60-second account cooldown, and no unresolved account writes. Retries never create a
   second comment for the same parent, even with revised text or another task.
4. POST only to the fixed origin, without redirects/retries. Record accepted,
   pending_verification, rejected, or unknown. Timeouts/5xx/malformed receipts are
   unknown and block further account writes. Store hashes and the outgoing text,
   never the API key or raw server response. Honor numeric Retry-After on 429.
5. A separate guarded verification tool accepts only action ID and numeric answer;
   code, account, target, and task are recovered from the ledger. One verification
   attempt, recorded before sending. Accept only matching comment receipts.
6. `/schedule actions [task]` exposes receipts; `/schedule resolve-action <action>
   accepted <remote-id>` or `rejected` records a human reconciliation after the
   originating run has ended. It never resends or deletes an intent.
7. Update operational outcome classification, history context and skill to expose
   pending/unknown actions honestly. Update README with opt-in setup and limits.

## Verification

Test all defaults denied, grant/revoke and task isolation, real gate/thread context,
atomic concurrent reservation/budgets, duplicate suppression across restart, secret
redaction, ownership failures, redirect refusal, ambiguous network results, 429
backoff, challenge/receipt validation and human reconciliation. Run the full suite,
review independently, then commit/push/open PR. Real HTTP verification is read-only;
live writes and unattended rollout are not part of this implementation check.

An accepted receipt is not a visibility guarantee. Generic new posts, arbitrary
comments, DMs, votes, follows and account changes remain outside this scope.

## Validation result

933 full-suite tests passed; diff check passed; independent review found no
remaining material issues. Live read-only checks confirmed the authenticated
account, own-post author, and comment-tree response shapes. No live POST was
sent and no live task grant was created. Verification uses mocked HTTP receipts;
real write behavior and public visibility remain unverified until an authorized
rollout. User instructions and tool descriptions now agree on the narrow scope.
