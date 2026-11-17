# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

Vegapunk is an open-source tool for managing homelab hosts and services. It has two components:

- **Web interface** — central UI where users view and manage hosts and services.
- **Agent** — a CLI agent installed on each host that executes actions requested by the web interface.

The project is in early development; the tech stack and layout are not decided yet. Update this file as decisions are made.

## Working Agreements

- Keep changes small and focused; prefer simple solutions over premature abstraction.
- Ask before introducing a new language, framework, or major dependency — and record the decision here once made.
- The agent runs with access to users' machines: treat security as a first-class concern (authenticate agent ↔ server traffic, validate all inputs, never execute unvalidated commands, avoid storing secrets in plaintext).
- Write tests alongside new functionality.
- Keep `README.md` user-facing and brief; put contributor/dev details here or in `docs/`.
- Do not commit or push unless asked.

## Architecture

_TBD — document components, communication protocol between web and agent, and data storage as they are designed._

## Commands

_TBD — add build, test, lint, and run commands once the stack is chosen._
