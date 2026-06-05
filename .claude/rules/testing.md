# Testing

## What "tested" means
- A change is done only when the relevant tests and type checks/build pass locally.
- Run the narrowest relevant suite while iterating; run the broader suite before declaring done.
- TS: `vitest`/`jest` (per repo). Python: `pytest`. Match whatever the repo already uses.

## Writing tests
- Add or update tests for behavior you change. Cover the happy path **and** the failure/edge
  cases that matter (empty, null, boundary, error propagation).
- Test behavior and public contracts, not private implementation details.
- Tests must be deterministic: no real network/time/randomness — fake or inject them.
- One clear assertion focus per test; name tests by the behavior they pin down.

## Discipline
- **Never** delete, skip (`.skip`/`xfail`), or weaken a test just to get a green build. If a test
  is genuinely wrong, fix it deliberately and say why.
- A failing test is a finding — surface it with the output, don't hide it.
- Red build = not done. Don't commit over it.
