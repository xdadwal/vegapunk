# AGENTS.md

Guidance for AI coding agents (Claude Code, Codex, and others) working in this repository. This is the primary file; `CLAUDE.md` only points here.

## Project

Vegapunk is an open-source tool for managing a homelab host and its services through a web interface.

It is a single server that runs on the host it manages, and it manages only that host. It serves the web interface and periodically captures data from that host, then stores it for later use. There is no separate agent: a dedicated host agent was considered and dropped (2026-11-26).

The project is in early development: the repo holds only a skeleton, and the first-version scope is not decided yet. Update this file as decisions are made.

## Tech stack

- **Backend** (`backend/`): Python 3.12, FastAPI, SQLite. Dependencies managed with uv; ruff for lint and format; pytest for tests.
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

One server process on the host: web UI, API, scheduler, collectors, and storage.

```
scheduler -> collectors (read the local host) -> ingest -> storage -> API -> web UI
```

- **One ingest path.** Collectors turn whatever they read into normalized records (host facts, service state, metrics, logs) and hand them to a single ingest entry point.
- **Nothing downstream knows how data was captured.** Storage, API, and web UI depend only on the normalized data model, never on a collector.
- **Collectors sit behind an interface.** The scheduler runs them without knowing what they read, so new data sources can be added without touching the rest.
- **Ingest is safe to repeat.** Records carry the time they were observed, and ingesting the same record twice has no extra effect.

_TBD — the data model, and what is captured and how often._

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
