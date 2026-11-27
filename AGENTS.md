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

**Data model** (see `backend/vegapunk/migrations/`): `host` (one row of facts), `host_metrics` and `container_metrics` (samples; `res` 0 is raw, 300 is a 5-minute average; `ts` is Unix seconds), `containers` (one row per Docker id, with `removed_at` once it disappears from a complete listing), `capture_status` (per source: last attempt, last success, last error), and, from migration 0004, `findings`, `finding_evidence`, `check_settings`, `investigations` and `container_restarts`, plus `containers.oom_killed` and `containers.exit_marker`; migration 0005 adds `check_settings.watch_enabled`.

## Commands

Backend, from `backend/`:

- `uv sync` — install dependencies
- `uv run uvicorn vegapunk.main:app --reload` — run the API on http://localhost:8000
- `uv run pytest` — run tests
- `uv run ruff check .` and `uv run ruff format .` — lint and format
- `uv run python -m vegapunk reset-password` — set a new admin password (reads the same data directory)

Frontend, from `frontend/`:

- `npm install` — install dependencies
- `npm run dev` — run the dev server; requests to `/api` are proxied to the backend
- `npm test` — run tests (Vitest and Testing Library)
- `npm run lint` — lint
- `npm run build` — type-check and build

Whole app: `docker compose up --build` builds one image that serves the API and the built frontend on port 8000.

CI (`.github/workflows/ci.yml`) runs lint, tests, and build for both, and builds the image, on every pull request.

## Conventions

- **Database:** stdlib `sqlite3` with numbered SQL migrations in `backend/vegapunk/migrations/`. Add a new file (`0002_name.sql`); never edit one that has shipped. Startup refuses a database written by a newer version.
- **Configuration:** environment variables read once in `config.py`: `VEGAPUNK_DATA_DIR` (default `data`), `VEGAPUNK_STATIC_DIR` (the built frontend; set in the image), `VEGAPUNK_SECURE_COOKIES` (`1` behind HTTPS), `VEGAPUNK_CAPTURE_INTERVAL` (seconds, default 15), `VEGAPUNK_DOCKER_URL` (default the local socket), `VEGAPUNK_DISK_PATH` (the disk measured for Storage, default `/`). The AI provider is configured in the app (Settings), not by environment. `Settings.capture` is false unless built by `load_settings`, so tests never start the scheduler unless they ask.
- **Reading the host:** only `backend/vegapunk/host/` talks to Docker or the system, and `host/docker.py` exposes reads only (a test pins its public methods). Everything else works from records.
- **Capture:** `capture/collectors.py` turns what the host modules read into the normalized records in `capture/records.py`; `capture/ingest.py` is the only code that stores them; `capture/scheduler.py` runs the collectors every interval in one thread and records per-source status; `capture/rollup.py` keeps raw samples for a day and 5-minute averages for 30 days. A new data source is a new collector. Run one uvicorn worker.
- **Container status:** `capture/status.py` (`container_status`) is the one place Docker states become online, degraded, offline or unknown: running is online (degraded if unhealthy); restarting and paused are degraded; exited and dead are offline; anything else is unknown.
- **Container identity:** keyed by Docker id in the database, by name in URLs. A recreated container starts a fresh history. Names must match Docker's own rule, so encoded slashes never need to work.
- **The agent (`backend/vegapunk/ai/`):** `operator.py` builds a logpose `Agent` for each answer from the saved settings and streams it as JSON lines; `tools.py` is its read-only tool set (pinned by a test; adding a tool that writes is a deliberate, reviewed step, behind the permission levels); `gate.py` is the one gate every tool call passes through (it allows the six tools and logpose's built-in `recall_tool_output`, and caps lookups per answer); `prompt.py` is what the agent is told; `store.py` holds the AI settings and conversation list; `redact.py` is the redaction.
- **Anything from a container that can reach the AI provider passes through `redact`**, as a whole text (a private key spans lines). Text from containers is data, never instructions, and the system prompt says so.
- **The API key** is encrypted with `crypto.py` (key file `secret.key`, mode 0600, written so a crash cannot leave it empty, never regenerated if invalid). It is never returned by an API, logged, or put in an error: provider errors go through `scrub_secret`. A saved key is only used with the base URL it was saved for.
- **logpose** is pinned to its `v0.2.0` tag in `pyproject.toml` and `uv.lock`; the Dockerfile has a builder stage with `git` for it. Conversations are logpose sessions in `conversations.db`; `vegapunk.db` keeps the list.
- **Findings (`backend/vegapunk/watch/`):** `rules.py` holds the cheap rules that decide when to call the model; `investigate.py` runs the model and keeps only evidence lines that are whole lines of what `container_logs` returned; `runner.py` is the single asyncio loop, with a 6 h cooldown per subject, a cap of 20 investigations per 24 h and a 30 min back-off after a failure, all recorded in `investigations`. Statuses are open, resolved and reviewed. Metric evidence is the rule's own fact line, never written by the model. `check_settings.enabled` gates only the daily check; `check_settings.watch_enabled` (migration 0005, default on) gates only signal-triggered investigations, read fresh each tick. With the watch off the rules still run, so open findings still resolve and the cooldowns still hold.
- **API:** everything is under `/api`. Every route except health, setup, and sign-in requires the signed-in admin (`require_admin`).
- **Frontend styling:** design tokens in `frontend/src/styles/tokens.css` and component styles in `components.css`, both taken from the design system. Use the tokens; do not hard-code colors, spacing, or fonts.
