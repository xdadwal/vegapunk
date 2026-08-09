---
name: debug-failure
description: Reproduce a failing test, exception, regression, or incorrect behavior; isolate and fix its root cause; add regression coverage; and verify the minimal repair. Use when the cause is not already obvious.
---

# debug-failure — prove and fix the root cause

Debug from reproducible evidence. Fix the underlying cause rather than masking its symptom.

## Delegation

If the host exposes native subagents, first delegate reproduction and root-cause investigation to a
fresh subagent with a bounded failure report and ask for evidence, not edits. The parent agent
retains ownership of the fix unless the user explicitly requested delegated implementation. Wait
for and validate the investigation before editing.

If no delegation capability exists, investigate in the current agent. Do not install delegation
software or add provider-specific files implicitly.

## Method

1. **Reproduce:** Run the smallest reliable failing command and capture the exact symptom.
2. **Form hypotheses:** List only plausible causes supported by the code path or environment.
3. **Isolate:** Use targeted reads, assertions, logging, history, or narrowed tests to eliminate
   hypotheses.
4. **Confirm:** Identify the root cause with `path:line` evidence and explain why it produces the
   symptom.
5. **Fix:** Make the smallest targeted change that corrects the cause without drive-by refactoring.
6. **Regress:** Add or strengthen a test that fails for the original defect when feasible.
7. **Verify:** Re-run the original reproduction, nearby tests, and required broader checks.
8. **Clean up:** Remove temporary instrumentation and review the final diff.

Treat environment, dependency, and configuration failures honestly; do not force a code change when
the defect is external. Never hide errors behind broad exception handling or fabricated fallback
values.

## Output contract

- **Symptom:** What failed.
- **Reproduction:** Exact command or steps.
- **Root cause:** `path:line` and the evidence proving it.
- **Fix:** What changed and why it addresses the cause.
- **Regression coverage:** Test added or why one was not feasible.
- **Verification:** Commands and outcomes.
- **Follow-ups:** Remaining risk or external action, if any.
