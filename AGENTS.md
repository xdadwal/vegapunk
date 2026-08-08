# Vegapunk engineering guide

## Working approach

- Plan before large changes. Reuse existing patterns and utilities before adding new ones.
- Tests gate “done”: run the relevant tests/build, review the diff, and report any skipped or failing check.
- Prefer the smallest reversible change that solves the problem. Ask only when genuinely ambiguous; otherwise state the obvious default you chose.
- Treat recurring user feedback as a possible process improvement: propose a specific rule, skill, or hook update; never change shared configuration silently.

## Engineering standards

- Match surrounding naming, structure, and comment density. Prefer clarity over cleverness; name by intent and write comments for *why*.
- Do not silently swallow errors or fabricate fallbacks that conceal a failure. Validate external inputs and report outcomes faithfully.
- Avoid new abstractions for one caller, keep functions focused, and prefer explicit data flow. Do not mix drive-by refactors into feature work.
- Never hardcode or log secrets/PII; treat external input as untrusted. Prefer existing dependencies or the standard library before adding one.

## Git

- Never commit or push directly to `main`/`master`; use a focused `feat/`, `fix/`, `chore/`, or `docs/` branch.
- Commit only when requested. Use Conventional Commits (`type(scope): summary`), stage intended files explicitly, and never commit secrets or generated artifacts.
- Rebase/pull the base branch before opening a PR. Use `--force-with-lease`, never a blind force push.

## Testing

- Add or update tests for changed behavior, including meaningful failure or edge cases. Test public contracts, keep tests deterministic, and run the narrowest suite while iterating.
- Never delete, skip, or weaken a test just to get green. A red build is a finding, not completion.

## Python

- Type-hint public functions and dataclasses; catch narrow exceptions deliberately. Prefer `pathlib`, context managers, and typed models at boundaries.
- Use the project virtual environment: `.venv/bin/python -m pytest -q` for tests and `.venv/bin/python -m vegapunk` to run the agent.
- Use the repository’s formatter/linter; avoid mutable defaults and blocking I/O inside async code.

## TypeScript and React

- Keep TypeScript strict: no unjustified `any`, ignored errors, or non-null assertions. Give exported APIs explicit types.
- Use `async`/`await`, handle errors, keep modules and components cohesive, and favor composition plus small pure functions.
- In React, use function components and hooks correctly; give list items stable keys.

## Vegapunk tools

- Tools are type-hinted functions decorated with `@tool` and registered via `vegapunk/tools/__init__.py`. Write model-oriented docstrings with Google-style `Args:` blocks and return a `str`.
- Keep tool output factual. Mark irreversible or high-impact tools with `@tool(guarded=True)` so approval remains fail-closed.
- Be forgiving of loose model inputs for lookups/searches. Verify tool work with the project test suite and a real run.
