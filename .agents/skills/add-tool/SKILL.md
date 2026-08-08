---
name: add-tool
description: Add a Vegapunk tool using the project’s typed @tool convention, registration, approval policy, tests, and documentation.
---

# add-tool — add a Vegapunk capability

Use this workflow whenever adding a callable capability under `vegapunk/tools/`.

## 1. Inspect existing patterns

Read `vegapunk/tools/registry.py`, `vegapunk/tools/__init__.py`, and the closest existing tool and
tests. Reuse their structure rather than introducing a new abstraction for one tool.

## 2. Implement the tool

Create `vegapunk/tools/<name>.py`, keeping tightly related tools together when that is clearer.

- Decorate the function with `@tool` from `.registry`.
- Type-hint every parameter and the `str` return value. Logpose derives and validates the input
  schema from these annotations; never hand-write JSON Schema.
- Write the docstring for the model: explain when to call the tool and document every parameter in
  a Google-style `Args:` block.
- Return factual, useful text. Keep assistant personality out of tool output.
- Handle expected environmental failures with a clear result; allow unexpected defects to surface
  through the agent loop rather than silently concealing them.
- Be forgiving where model-supplied lookup/search text can reasonably vary in case or wording.

Example:

```python
from .registry import tool


@tool
def get_weather(city: str) -> str:
    """Look up current weather for a city.

    Args:
        city: City whose weather should be returned.
    """
    ...
```

## 3. Apply the approval boundary

Use `@tool(guarded=True)` for irreversible or high-impact behavior, including shell execution,
file writes/deletes, sending, spending, or side-effecting network calls. Approval is fail-closed:
a guarded tool without an approver must not run.

Read-only tools use bare `@tool`.

## 4. Register it

Import the new module in `vegapunk/tools/__init__.py`. Importing runs the decorator and adds the tool
to `REGISTRY`; do not maintain a separate schema or manual tool list.

## 5. Test the contract

Add focused tests under `tests/` for:

- registration, name, description, and parameter schema when relevant;
- successful behavior;
- meaningful failure and edge cases;
- guarded status for any side-effecting tool;
- workspace confinement or approval behavior when applicable.

Test public behavior rather than private implementation details.

## 6. Keep documentation accurate

If the capability is reader-facing, update the README’s tool table and any related configuration or
usage text. Do not document behavior that is not wired into the registry.

## 7. Verify

Run:

```bash
.venv/bin/python -m pytest -q
```

Also exercise the real tool path when practical. Guarded tools need a real terminal approver or a
test/driver with an explicit approving `Approver`; they are denied without one. Review the final
diff and report any check that could not be run.
