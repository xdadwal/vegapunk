---
name: explore-codebase
description: Explore an unfamiliar codebase, trace behavior end to end, and return a concise evidence-based map with file references. Use for locating code, understanding data flow, or identifying the smallest change surface.
---

# explore-codebase — map code without changing it

Resolve codebase questions with focused, read-only exploration. Return conclusions and evidence,
not a dump of file contents.

## Delegation

If the host exposes a native subagent or delegation capability, delegate this bounded exploration
to one fresh read-only subagent and ask it to return the output contract below. Wait for its result
and validate important claims before presenting them. Do not delegate merely by starting another
shell process or installing an extension.

If no subagent capability is available, perform the same workflow in the current agent. A skill is
an instruction package, not a spawning mechanism by itself.

## Method

1. Identify the stack, entry points, project layout, and test configuration.
2. Search broadly for relevant symbols, filenames, routes, commands, or configuration.
3. Read only the definitions, call sites, tests, and configuration needed to answer the question.
4. Trace the real data and control flow across module boundaries.
5. Stop once the evidence answers the question confidently.

For Python, look for entry points, services, data/storage boundaries, settings, and pytest layout.
For TypeScript or React, look for entry points, routing, state, components, data fetching, and build
or test configuration. Prefer repository-specific evidence over assumed framework conventions.

## Output contract

- **Answer:** Resolve the question in two to five sentences.
- **Key locations:** `path:line — purpose` for the few relevant locations.
- **How it connects:** The essential data/control flow, when useful.
- **Change surface:** The minimal files or functions a likely change should touch.
- **Unknowns:** Anything not confirmed by available evidence.

## Constraints

Do not edit files. Do not paste large file contents. Distinguish confirmed behavior from inference,
and label anything unconfirmed rather than speculating.
