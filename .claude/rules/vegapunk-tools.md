---
description: Conventions for Vegapunk tools
paths:
  - "vegapunk/tools/**"
---

# Vegapunk tool conventions

Tools are Vegapunk's capabilities. Each is a type-hinted function decorated with `@tool` (from
`vegapunk/tools/registry.py`); the decorator derives the schema from the type hints, the name from
the function, and the description from the docstring, and auto-registers it into `REGISTRY`. The
schema work is logpose's — `@tool` wraps `logpose.tool` and adds the one thing it has no concept of,
which tools need approval.

- **Type-hint every parameter.** The input schema is derived from hints, and an unannotated one is
  rejected at import — a loud failure while you're writing the tool, not a wrong schema the model
  has to guess against. `str`, `int`, `float`, `bool`, `list`, `dict`, `Literal`, `Optional`, and
  pydantic models all work.
- **Docstring = tool description, written for the model.** State *when* to call it, not just what it
  does. Document each parameter in a Google-style `Args:` block: those descriptions reach the model
  as part of the schema, and a small model leans on them.
- **Return a `str`** — that's the tool result the model observes.
- **Tools stay factual.** Personality/mood belongs in the system prompt (`vegapunk/config.py`), not
  in tool output, so tools remain reusable.
- **Register** by importing the module in `vegapunk/tools/__init__.py`.
- **Guard risky tools** with `@tool(guarded=True)`. Irreversible/high-impact actions (shell, file
  writes/deletes, sending, spending, side-effecting network calls) must go behind the approval gate
  (`vegapunk/gate.py`) — never execute silently. Guarded names live in `GUARDED`; the gate is
  fail-closed, so a guarded tool with no approver is blocked rather than run.
- **Expected failures return a clear string** (e.g. "No battery detected"); unexpected errors may
  raise — the loop catches them and feeds the message back to the model. Bad arguments never reach
  you: they come back to the model as a validation error it can correct.
- **Be lenient with model-supplied inputs.** The local model often passes loose args (wrong case,
  extra words, a phrase instead of a keyword); for lookups/searches prefer forgiving matching —
  case-insensitive and partial — so a reasonable request still succeeds (e.g. name search finds
  `PROMPT.md` from "prompt").
- **Verify** with `.venv/bin/python -m pytest -q` and a real run before calling it done. A guarded
  tool needs a real terminal (`CLIApprover` auto-denies without a TTY) — or a driver that passes an
  auto-approving `Approver` to the `Session`.

To scaffold a new tool consistently, use the `/add-tool` skill.
