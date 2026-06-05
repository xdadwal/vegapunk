---
description: TypeScript / frontend conventions
paths:
  - "**/*.ts"
  - "**/*.tsx"
  - "**/*.mts"
  - "**/*.cts"
---

# TypeScript & frontend

## Types
- `strict` mode is the baseline. No `any` as an escape hatch — use `unknown` + narrowing, or a
  precise type. No `@ts-ignore`/`@ts-expect-error` without a comment saying why.
- Model state so illegal states are unrepresentable: discriminated unions over boolean soups,
  `readonly` where things shouldn't mutate, `as const` for literals.
- Prefer inference for locals; annotate exported/public function signatures explicitly.
- Avoid non-null `!` assertions; narrow instead.

## Style & structure
- ESLint + Prettier are the authority on formatting and lint — don't hand-fight them
  (format-on-save runs automatically). Fix the cause, not the symptom, of a lint error.
- Prefer composition and small pure functions over inheritance and large components.
- Keep modules cohesive; export the minimum. Co-locate a component with its styles/tests.

## Async & errors
- `async`/`await` over raw `.then()` chains. Always handle rejection paths.
- Don't swallow promise rejections; no floating promises (await or explicitly `void`).
- Throw `Error` (or subclasses) with useful messages, not strings.

## React (when present)
- Function components + hooks. Respect the rules of hooks; give effects correct, minimal deps.
- Keep components presentational where possible; push logic into hooks/utilities.
- Lists need stable keys; avoid index keys for reorderable lists.
