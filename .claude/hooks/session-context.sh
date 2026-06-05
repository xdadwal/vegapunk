#!/usr/bin/env bash
#
# SessionStart — inject current git branch + working-tree status as additional context.
# Emits JSON: { hookSpecificOutput: { hookEventName, additionalContext } }.
#
set -uo pipefail

PROJ="${CLAUDE_PROJECT_DIR:-$PWD}"
g() { git -C "$PROJ" "$@" 2>/dev/null; }

branch="$(g symbolic-ref --short HEAD 2>/dev/null || g rev-parse --short HEAD 2>/dev/null || true)"
status="$(g status --short 2>/dev/null | head -n 40 || true)"

python3 - "$branch" "$status" <<'PY'
import sys, json
branch = sys.argv[1] if len(sys.argv) > 1 else ""
status = sys.argv[2] if len(sys.argv) > 2 else ""
lines = []
if branch:
    lines.append(f"Git branch: {branch}")
if status.strip():
    lines.append("Uncommitted changes:\n" + status.rstrip())
else:
    lines.append("Working tree is clean.")
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "\n".join(lines),
    }
}))
PY

exit 0
