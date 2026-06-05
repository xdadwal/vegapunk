#!/usr/bin/env bash
#
# PreToolUse(Bash) guardrail — block clearly destructive shell commands.
# Reads the hook JSON on stdin. Exit 2 with a reason on stderr = block (Claude sees the reason).
# This fires BEFORE permission-mode checks, so it holds even under --dangerously-skip-permissions.
#
set -uo pipefail

input="$(cat)"
cmd="$(printf '%s' "$input" | python3 -c 'import sys,json
try:
    print(json.load(sys.stdin).get("tool_input",{}).get("command","") or "")
except Exception:
    pass' 2>/dev/null || true)"
[[ -z "$cmd" ]] && exit 0

# collapse whitespace for matching
c="$(printf '%s' "$cmd" | tr '\n\t' '  ' | tr -s ' ')"

block() { echo "Blocked by ccc guard-bash: $1" >&2; exit 2; }

# recursive delete of a dangerous root (allows e.g. `rm -rf node_modules`, `rm -rf ./build`)
if printf '%s' "$c" | grep -Eq 'rm[[:space:]]+(-[A-Za-z]*[rRfF][A-Za-z]*[[:space:]]+)+(/|/\*|~|~/|\$HOME|\$\{HOME\}|\.|\.\.|\*)([[:space:]]*$|[[:space:]]+-)'; then
  block "recursive delete of a dangerous path (/, ~, \$HOME, ., .., *)"
fi

# fork bomb
printf '%s' "$c" | grep -Eq ':\(\)[[:space:]]*\{[[:space:]]*:\|:' && block "fork bomb"

# filesystem / disk destruction
printf '%s' "$c" | grep -Eq '\bmkfs(\.[a-z0-9]+)?\b'      && block "mkfs (formats a filesystem)"
printf '%s' "$c" | grep -Eq '\bdd\b[^|]*[[:space:]]of=/dev/' && block "dd writing to a device"
printf '%s' "$c" | grep -Eq '>[[:space:]]*/dev/(sd|nvme|disk|hd)' && block "overwriting a block device"

# chmod/chown -R on root
printf '%s' "$c" | grep -Eq 'chmod[[:space:]]+(-[A-Za-z]+[[:space:]]+)*[0-7]{3,4}[[:space:]]+/([[:space:]]|$)' && block "chmod on /"
printf '%s' "$c" | grep -Eq 'cho(wn|wn)[[:space:]]+-[A-Za-z]*R[A-Za-z]*[[:space:]].*[[:space:]]/([[:space:]]|$)' && block "recursive chown on /"

# piping a download straight into a shell
printf '%s' "$c" | grep -Eq '(curl|wget|fetch)[[:space:]][^|]*\|[[:space:]]*(sudo[[:space:]]+)?(sh|bash|zsh|fish)\b' && block "piping a remote download into a shell"

# hard force-push (allow --force-with-lease)
if printf '%s' "$c" | grep -Eq '\bgit[[:space:]]+push\b' \
   && printf '%s' "$c" | grep -Eq '(^| )(--force|-f)( |$)' \
   && ! printf '%s' "$c" | grep -Eq 'force-with-lease'; then
  block "hard git force-push — use --force-with-lease, and not on shared branches"
fi

exit 0
