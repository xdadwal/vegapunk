---
description: Python / backend conventions
paths:
  - "**/*.py"
  - "**/*.pyi"
---

# Python & backend

## Types & correctness
- Type-hint all public functions and dataclasses; `mypy` (or `pyright`) clean is the target.
- No bare `except:` and no `except Exception: pass`. Catch the narrowest exception you can
  handle, add context, and re-raise or handle deliberately.
- Prefer `dataclasses`/`pydantic` models over passing around dicts. Validate at the edges.
- Use `pathlib` over string paths; context managers (`with`) for files/connections/locks.

## Style & tooling
- `ruff` (lint + format) is the authority — or `black` if the repo uses it. Formatting runs
  automatically on save; fix the underlying lint cause rather than suppressing it.
- Follow PEP 8 naming: `snake_case` functions/vars, `PascalCase` classes, `UPPER_CASE` consts.
- f-strings for formatting. No mutable default arguments (`def f(x=[])` is a bug).

## Structure & dependencies
- Keep functions small and pure where practical; isolate I/O at the boundaries.
- Manage env/deps with the repo's tool (`uv`/`poetry`/`pip-tools`); don't hand-edit lockfiles.
- Don't rely on import side effects; guard scripts with `if __name__ == "__main__":`.

## Async & web
- Don't block the event loop in `async` code (no sync I/O inside coroutines).
- Validate and type request/response bodies (e.g. pydantic) at API boundaries; return explicit
  status codes; never leak stack traces or secrets to clients.
