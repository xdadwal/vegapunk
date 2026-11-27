## What and why

<!-- What does this change, and why? Keep it to a few sentences. -->

<!-- The issue this addresses, if any, e.g. "Fixes #12". -->

## How it was tested

<!-- What you ran, and what you checked by hand (screens, commands, platform). -->

## Checklist

- [ ] Tests are added or updated for the change.
- [ ] Backend: `uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest --cov` pass.
- [ ] Frontend: `npm run lint`, `npm run format:check`, `npm run test:coverage` and `npm run build` pass.
- [ ] Docs are updated (README, docs/, CHANGELOG.md under Unreleased, known-issues.md) where behaviour changed.
- [ ] UI changes use the design tokens, with no hard-coded colours, spacing or fonts, and say "Agent", not "AI".
- [ ] No new dependency, or it was discussed and agreed in the linked issue.
- [ ] A schema change is a new migration file; no shipped migration was edited.
- [ ] Secrets and redaction were considered: nothing logs, returns or stores a secret, and container text reaching the AI provider passes through `redact`.
- [ ] It fits the first-version scope (observer only) or was agreed in an issue first.
