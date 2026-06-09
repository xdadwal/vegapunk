---
name: add-tool
description: Add a new tool (capability) to the Vegapunk agent following the project's @tool convention — typed function, auto-derived schema, registration, and verification. Use whenever adding any new capability to Vegapunk.
---

# add-tool — add a capability to Vegapunk

Vegapunk's capabilities are **tools**: a plain, type-hinted Python function decorated with `@tool`
(from `vegapunk/tools/registry.py`). The decorator derives the JSON Schema from the type hints, the
name from the function, and the description from the docstring, then auto-registers it. Follow these
steps so every tool is consistent.

## Steps

1. **Create the module.** Add `vegapunk/tools/<name>.py`. One tool per file unless tools are tightly
   related.

2. **Write the function.**
   - Type-hint every parameter — the schema is derived from hints; an unannotated param silently
     defaults to `string`. Supported hint → schema types: `str`, `int`, `float`, `bool`, `list`,
     `dict`.
   - Give it a clear one-line docstring written *for the model* — it becomes the tool description
     and should say *when* to call it, not just what it does ("Call this when…").
   - Return a `str` (what the model sees as the tool result).
   - Decorate with `@tool`:
     ```python
     from .registry import tool

     @tool
     def get_weather(city: str) -> str:
         """Look up the current weather for a city. Call this when the user asks about weather."""
         ...
     ```

3. **Keep the tool factual.** Tools report facts / perform actions; Vegapunk's *personality* lives
   in the system prompt (`vegapunk/config.py`), not in tool output. This keeps tools reusable.

4. **Register it.** Add the module to the import line in `vegapunk/tools/__init__.py`
   (`from . import battery, clock, <name>`) — importing it runs the `@tool` decorator.

5. **Guard risky tools.** If the tool is irreversible or high-impact (runs shell, writes/deletes
   files, sends messages, spends money, or makes side-effecting network calls), it MUST go behind a
   confirmation gate — never let it execute silently. If no gate pattern exists yet, confirm its
   design with the user before adding the tool.

6. **Verify (required — tests gate "done").**
   - `.venv/bin/python -m pytest -q`
   - `.venv/bin/python try_agent.py "<a question that should trigger the tool>"` and confirm the
     `[tool] <name>(...)` trace line fires with a sensible result.

7. **Test the interesting logic.** If the tool has non-trivial parsing or branching, add a unit test
   under `tests/`. Tools and the loop are testable without a model — use stubbed inputs.

## Notes
- Expected failure cases should return a clear, actionable string (e.g. "No battery detected")
  rather than raising. Unexpected errors may raise — the loop catches them and feeds the message
  back to the model, so a tool bug never crashes the run.
- Never hand-write JSON Schema; rely on the type hints.
