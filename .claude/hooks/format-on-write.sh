#!/usr/bin/env bash
#
# PostToolUse(Edit|Write|MultiEdit) — best-effort auto-format the changed file.
# PostToolUse cannot block; this only tidies output and never fails the session.
#
set -uo pipefail

input="$(cat)"
fp="$(printf '%s' "$input" | python3 -c 'import sys,json
try:
    ti = json.load(sys.stdin).get("tool_input",{})
    print(ti.get("file_path") or ti.get("path") or "")
except Exception:
    pass' 2>/dev/null || true)"
[[ -z "$fp" || ! -f "$fp" ]] && exit 0

have() { command -v "$1" >/dev/null 2>&1; }

case "$fp" in
  *.ts|*.tsx|*.js|*.jsx|*.mjs|*.cjs|*.json|*.jsonc|*.css|*.scss|*.less|*.html|*.md|*.mdx|*.yaml|*.yml)
    if have prettier; then prettier --write "$fp" >/dev/null 2>&1 || true
    elif have npx;     then npx --no-install prettier --write "$fp" >/dev/null 2>&1 || true
    fi ;;
  *.py)
    if have ruff;  then ruff format "$fp" >/dev/null 2>&1 || true; ruff check --fix "$fp" >/dev/null 2>&1 || true
    elif have black; then black "$fp" >/dev/null 2>&1 || true
    fi ;;
  *.go)  have gofmt   && { gofmt -w "$fp"   >/dev/null 2>&1 || true; } ;;
  *.rs)  have rustfmt && { rustfmt "$fp"    >/dev/null 2>&1 || true; } ;;
esac

exit 0
