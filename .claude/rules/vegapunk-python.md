---
description: How to run Python in the Vegapunk repo (use the project's .venv)
paths:
  - "**/*.py"
  - "**/*.pyi"
---

# Running Python in Vegapunk

- **Run Python through the repo's `.venv`** — a bare `python` is missing the deps.
  - Tests: `.venv/bin/python -m pytest -q`
  - Run the agent: `.venv/bin/python -m vegapunk`
