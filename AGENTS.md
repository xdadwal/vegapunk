# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, and others) working in this repository. This is the primary file; `CLAUDE.md` only points here.

## Project

Vegapunk is an open-source tool for managing a homelab host and its services through a web interface.

It is a single server that runs as a Docker container on the host it manages, and it manages only that host. It serves the web interface, periodically captures data from the host, and runs an AI operator that watches that data and reports problems. There is no separate agent: a dedicated host agent was considered and dropped (2026-11-26).

The project is in early development: the repo holds only a skeleton. Update this file as decisions are made.

## First-version scope

Decided 2026-11-26. Ask before going beyond it.

- **Observer mode only.** Vegapunk reads and reports; it changes nothing on the host. Containers are read-only: no start, stop, or restart.
- **Services are Docker containers only.**
- **One admin account with login.** No other users or roles.
- **AI operator.** Runs inside the server. It raises findings (diagnosis plus evidence) from scheduled and background checks, and answers questions in one chat. It can suggest a fix in words but never carries one out.
- **AI provider.** Any OpenAI-compatible endpoint, with the user's own API key, one provider at a time. Integration goes through [logpose](https://github.com/xdadwal/logpose).
- **Capture.** Only the data the screens show or calculate from: container status, restart counts, and uptime; CPU and memory per container and for the host; storage used; enough history for trends and comparisons.
- **Logs are not stored.** The AI operator reads container logs live from Docker during a check; only the lines it cites as evidence are kept.
- **Screens.** Sign in, Setup (admin account, AI provider), Overview (the host), Containers, Container detail, Findings, Finding detail, Agent chat, History, Settings, Profile.
- **UI wording.** Say "Agent", not "AI", for the operator; keep "AI" only for provider settings.

Planned for later, so do not design it out: managing containers (start, stop, restart) and letting the agent run commands on the host to debug and act, gated by a permission level (observe only / ask first / auto-fix).

Out of scope: multiple hosts, a host agent, multiple users, non-Docker services.

## Tech stack

- **Backend** (`backend/`): Python 3.12, FastAPI, SQLite. Dependencies managed with uv; ruff for lint and format; pytest for tests.
- **AI operator:** [logpose](https://github.com/xdadwal/logpose) for the agent loop and provider integration, installed from a tagged GitHub release (it is not on PyPI).
- **Deployment:** one Docker container with the host's Docker socket mounted.
- **Frontend** (`frontend/`): TypeScript, React, Vite. Dependencies managed with npm; oxlint for lint.

## Working Agreements

- Keep changes small and focused; prefer simple solutions over premature abstraction.
- Ask before introducing a new language, framework, or major dependency — and record the decision here once made.
- The server runs on users' machines with access to their services: treat security as a first-class concern (least privilege, validate all inputs, never execute unvalidated commands, avoid storing secrets in plaintext).
- Write tests alongside new functionality.
- Keep `README.md` user-facing and brief; put contributor/dev details here or in `docs/`.
- Do not commit or push unless asked.

## Design decisions

- **Icons:** Lucide (ISC license), pinned to v0.460.0 so new icons match the existing drawing style. The design system uses a curated set of 52 icons drawn with a 1.5px stroke (1.75 at 16px).

## Architecture

One server process in a container on the host: web UI, API, scheduler, collectors, storage, and the AI operator.

```
scheduler -> collectors (read the local host) -> ingest -> storage -> API -> web UI
                                                              ^
                       AI operator (reads via tools) ---------'--> findings, chat
```

- **One ingest path.** Collectors turn whatever they read into normalized records and hand them to a single ingest entry point.
- **Nothing downstream knows how data was captured.** Storage, API, and web UI depend only on the normalized data model, never on a collector.
- **Collectors sit behind an interface.** The scheduler runs them without knowing what they read, so new data sources can be added without touching the rest.
- **Ingest is safe to repeat.** Records carry the time they were observed, and ingesting the same record twice has no extra effect.
- **The AI operator reaches the host only through tools.** Each tool is a small typed function (read container status, read logs, query stored metrics). The first version has read-only tools only.
- **Every tool call passes one gate.** The gate decides whether a call may run. Today it allows everything, since all tools are read-only. Later, action tools (restart a container, run a host command) are added as more tools, and the gate enforces the permission level and approvals. Adding actions must not require changing the operator loop, the API, or the UI structure.
- **Host access goes through one module.** All Docker and host calls live behind it, and in the first version it exposes read operations only. Mounting the Docker socket gives the container full control of Docker whatever the mount flags say, so read-only is enforced by this module, not by the mount.

_TBD — the data model, and how often data is captured._

## Commands

Backend, from `backend/`:

- `uv sync` — install dependencies
- `uv run uvicorn vegapunk.main:app --reload` — run the API on http://localhost:8000
- `uv run pytest` — run tests
- `uv run ruff check .` and `uv run ruff format .` — lint and format

Frontend, from `frontend/`:

- `npm install` — install dependencies
- `npm run dev` — run the dev server; requests to `/api` are proxied to the backend
- `npm run lint` — lint
- `npm run build` — type-check and build

CI (`.github/workflows/ci.yml`) runs lint, tests, and build for both on every pull request.
