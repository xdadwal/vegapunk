---
name: review-change
description: Review a working-tree, staged, branch, or pull-request diff for material correctness, security, error-handling, contract, and test issues. Use after implementation or before committing and report evidence-based findings by severity.
---

# review-change — evidence-based code review

Review changes for real defects and regressions. Avoid style churn and low-confidence speculation.

## Delegation

If the host exposes native subagents, delegate the review to a fresh subagent so it does not inherit
the implementation agent’s assumptions. Give it the task, intended behavior, relevant base, and the
output contract below; ask it not to edit. Wait for the result, verify material findings against the
code, and synthesize the final verdict.

If native delegation is unavailable, perform the review in the current agent. Do not install a
subagent extension or create provider-specific configuration without user approval.

## Process

1. Establish scope with `git status`, unstaged and staged diffs, or the requested branch/PR diff.
2. Read enough surrounding implementation and tests to understand each changed contract.
3. Review in priority order:
   - correctness and regressions;
   - swallowed errors or misleading fallbacks;
   - security, secrets, injection, authorization, and untrusted input;
   - API and compatibility breaks;
   - concurrency, cleanup, and resource-lifetime problems;
   - missing or ineffective tests;
   - violations of `AGENTS.md` and established local patterns.
4. Confirm each finding is caused or exposed by the change and cite exact evidence.
5. Ignore formatting and import-order issues owned by automated tooling.

## Severity

- **Critical:** Data loss, exploitable security issue, or common-path breakage.
- **High:** A concrete bug on a plausible path or a failure that will be hidden.
- **Medium:** A meaningful edge case, latent defect, or contract problem worth fixing.
- **Low:** Optional robustness or clarity improvement; omit unless genuinely useful.

## Output contract

List findings first, ordered by severity:

`path:line — **SEVERITY** — problem and impact — concrete fix`

Then provide:

- **Verdict:** `safe to commit` or `fix first`, naming blockers.
- **Testing gaps:** Only gaps that could allow a relevant regression.
- **Unknowns:** Missing context that prevented a conclusion.

If there are no material findings, say so plainly. Do not invent findings to fill the report.
