---
name: sync-config
description: Update this repo's Claude Code config to the latest from the ccc source checkout. Use when you want to pull in newer skills/agents/hooks/rules that have improved in ccc since this repo was seeded.
---

# sync-config — pull the latest config from ccc

This repo's `.claude/` was seeded from a `ccc` checkout. This re-runs `ccc`'s `bootstrap.sh` to
update it, preserving your personal `settings.local.json`.

## Steps

1. **Find the ccc source.** Read `.claude/.ccc-source` (written at seed time):
   ```bash
   cat .claude/.ccc-source
   ```
   If it's missing, ask the user for the path to their `ccc` checkout.

2. **Preview the changes** (dry run) so nothing is overwritten by surprise:
   ```bash
   bash "$(cat .claude/.ccc-source)/bootstrap.sh" --dry-run .
   ```
   Summarize what would change (new/updated skills, agents, hooks, rules, settings).

3. **Apply** once the user is happy:
   ```bash
   bash "$(cat .claude/.ccc-source)/bootstrap.sh" .
   ```

4. **Reconcile.**
   - `settings.local.json` is never touched — confirm any new keys you want from
     `settings.local.json.example`.
   - If your `CLAUDE.md` had local edits, bootstrap backed it up to `CLAUDE.md.bak`; re-apply
     anything custom and trim stack `@import`s this repo doesn't use.
   - Reload so new hooks/skills take effect (restart the session if needed).

## Notes
- The seeded `CLAUDE.md` header shows which `ccc` version this repo is on; compare to the source
  `VERSION` to know if an update is available.
- To update many repos, run `bootstrap.sh <repo>` for each from the `ccc` checkout.
