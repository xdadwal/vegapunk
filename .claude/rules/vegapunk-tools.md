---
description: Conventions for Vegapunk tools
paths:
  - "vegapunk/tools/**"
---

# Vegapunk tool conventions

Tools are Vegapunk's capabilities. Each is a type-hinted function decorated with `@tool` (from
`vegapunk/tools/registry.py`); the decorator derives the schema from the type hints, the name from
the function, and the description from the docstring, and auto-registers it into `REGISTRY`.

- **Type-hint every parameter.** The input schema is derived from hints — an unannotated param
  silently defaults to `string`. Supported: `str`, `int`, `float`, `bool`, `list`, `dict`.
- **Docstring = tool description, written for the model.** State *when* to call it, not just what it
  does.
- **Return a `str`** — that's the tool result the model observes.
- **Tools stay factual.** Personality/mood belongs in the system prompt (`vegapunk/config.py`), not
  in tool output, so tools remain reusable.
- **Register** by importing the module in `vegapunk/tools/__init__.py`.
- **Guard risky tools.** Irreversible/high-impact actions (shell, file writes/deletes, sending,
  spending, side-effecting network calls) must go behind a confirmation gate — never execute
  silently.
- **Expected failures return a clear string** (e.g. "No battery detected"); unexpected errors may
  raise — the loop catches them and feeds the message back to the model.
- **Verify** with `pytest -q` and a `try_agent.py "<triggering question>"` run before calling it
  done.

To scaffold a new tool consistently, use the `/add-tool` skill.
