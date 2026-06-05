#!/usr/bin/env bash
#
# PreToolUse(Edit|Write|MultiEdit) guardrail — block edits to secrets and lockfiles.
# Exit 2 with a reason = block.
#
set -uo pipefail

input="$(cat)"
fp="$(printf '%s' "$input" | python3 -c 'import sys,json
try:
    ti = json.load(sys.stdin).get("tool_input",{})
    print(ti.get("file_path") or ti.get("path") or "")
except Exception:
    pass' 2>/dev/null || true)"
[[ -z "$fp" ]] && exit 0

base="$(basename "$fp")"
block() { echo "Blocked by ccc protect-paths: $1 ($fp)" >&2; exit 2; }

# .env files — block real ones, allow example/template variants
case "$base" in
  .env.example|.env.sample|.env.template|.env.dist) ;;
  .env|.env.*) block "editing a real .env file (secrets belong in the environment)" ;;
esac

# key / certificate / credential material
case "$base" in
  *.pem|*.key|*.p12|*.pfx|*.keystore|*.jks|id_rsa|id_rsa.*|id_ed25519|id_ed25519.*|credentials|*.kdbx)
    block "editing a key/secret file" ;;
esac

# lockfiles — regenerate via the package manager, don't hand-edit
case "$base" in
  package-lock.json|pnpm-lock.yaml|yarn.lock|poetry.lock|Cargo.lock|uv.lock|composer.lock|Gemfile.lock|go.sum)
    block "editing a lockfile by hand — run the package manager to regenerate it instead" ;;
esac

exit 0
