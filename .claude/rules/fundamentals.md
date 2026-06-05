# Engineering fundamentals (language-agnostic)

These apply to every file in every repo.

## Clarity
- Write code that reads like the code around it: match the surrounding naming, structure, and
  comment density. Consistency beats personal preference.
- Name things by intent, not implementation. A good name removes the need for a comment.
- Prefer clarity over cleverness. If a reviewer needs a comment to understand *what* it does,
  rewrite it; reserve comments for *why*.

## Correctness & honesty
- **No silent failures.** Don't swallow errors with empty `catch`/`except`. Handle them, or let
  them propagate with context. A caught-and-ignored error is a hidden bug.
- Don't fabricate fallbacks that mask problems (e.g. returning empty/default data when a call
  fails) unless that behavior is explicitly intended and documented.
- Validate inputs at boundaries (user input, network, files); trust internal invariants.
- Report outcomes faithfully. If tests fail or a step was skipped, say so — don't paper over it.

## Change discipline
- Smallest change that solves the problem. Avoid drive-by refactors mixed into a feature change.
- Don't introduce a new abstraction for a single caller. Wait for the second or third use.
- Keep functions focused; prefer pure functions and explicit data flow over hidden state.
- Delete dead code rather than commenting it out — git remembers.

## Security
- Never hardcode secrets, tokens, or keys. Read them from the environment or a secrets manager.
- Never log secrets or full PII. Don't commit `.env` files (the hooks block editing them).
- Treat all external input as untrusted; parameterize queries, escape output, validate shapes.

## Dependencies
- Prefer the standard library and what's already in the project before adding a dependency.
- When you do add one, justify it briefly and pin/lock it via the package manager (don't
  hand-edit lockfiles — the hooks block that; regenerate them instead).
