# Developing Vegapunk

This page is for people working on Vegapunk's code. To install and run it, see the [install guide](install.md). For what a pull request needs, see [CONTRIBUTING.md](../CONTRIBUTING.md). The full set of rules for the code is in [AGENTS.md](../AGENTS.md).

- [How it fits together](#how-it-fits-together)
- [Repository layout](#repository-layout)
- [Running it locally](#running-it-locally)
- [Tests, lint and build](#tests-lint-and-build)
- [The Docker image and the smoke test](#the-docker-image-and-the-smoke-test)
- [The data directory and migrations](#the-data-directory-and-migrations)
- [Common changes](#common-changes)
- [How the tests work](#how-the-tests-work)
- [Debugging](#debugging)
- [Good places to start](#good-places-to-start)

## How it fits together

Vegapunk is one server process, running in one container on the host it watches. That process serves the web interface and the API, captures data from the host on a timer, stores it in SQLite, and runs the agent.

```
scheduler -> collectors (read the local host) -> ingest -> storage -> API -> web UI
                                                              ^
                       agent (reads via tools) ---------------'--> findings, chat
```

Every few seconds (15 by default) the scheduler runs each **collector**. A collector reads one source, such as Docker or the host's CPU and memory, and returns plain **records**. A single **ingest** function stores the records. The **API** and the **web interface** only ever see what is stored, so they don't know or care how it was captured.

The **agent** never touches the host directly. It works through a small set of read-only **tools** (read container status, read logs, query stored metrics), and every tool call passes through one **gate** that decides whether it may run. Today every tool only reads. Later, tools that act (restart a container, say) will be added behind that same gate and a permission level, without changing the rest.

A few rules follow from this, and reviews check them:

- Only `backend/vegapunk/host/` talks to Docker or the system, and it only reads. Mounting the Docker socket gives full control of Docker whatever the mount flags say, so read-only is enforced by that module.
- Ingest is safe to repeat: storing the same record twice has no extra effect.
- Anything from a container that can reach the AI provider passes through `redact` first.

## Repository layout

```
backend/              Python 3.12, FastAPI, SQLite (uv, ruff, pytest)
  vegapunk/
    main.py           the app uvicorn loads (`vegapunk.main:app`)
    app.py            builds the FastAPI app: routers, startup, the capture thread
    config.py         environment variables, read once
    db.py             connections, transactions and migrations
    crypto.py         encrypts the saved API key
    __main__.py       the command line: `reset-password` and `version`
    host/             the only code that talks to Docker (docker.py) or the system (system.py)
    capture/          collectors, records, ingest, the scheduler, rollups, container status
    watch/            the background watch: rules, the runner, investigations, findings storage
    ai/               the agent: operator, tools, gate, prompt, settings store, redaction
    api/              API routes and the payloads they return
    auth/             the admin account, passwords, sessions, sign-in routes
    migrations/       numbered SQL files (0001_init.sql, ...)
  tests/              pytest tests, fakes and helpers
frontend/             TypeScript, React, Vite (npm, oxlint, Vitest)
  src/
    App.tsx           the routes
    api.ts            the fetch wrapper every call goes through
    pages/            one file per screen (Overview, Containers, Findings, Agent, Settings, ...)
    components/       shared building blocks (Button, Input, StatusBadge, TimeSeriesChart, ...)
    auth/             the signed-in admin and sign-in state
    styles/           tokens.css (design tokens), components.css, base.css
docs/                 install guide, known issues, socket proxy, releasing, plans
scripts/smoke.sh      builds the image and checks it starts healthy
```

A little more on the backend packages:

- **`host/`**: `DockerHost` in `docker.py` wraps the Docker SDK and exposes reads only (ping, info, version, list containers, one-shot stats, logs); a test pins its public methods. `system.py` reads CPU, memory, disk and boot time with psutil.
- **`capture/`**: `collectors.py` turns what `host/` reads into the records in `records.py`. `ingest.py` is the only code that stores them. `scheduler.py` runs the collectors every interval in one thread and records each source's status in `capture_status`. `rollup.py` keeps raw samples for a day and 5-minute averages for 30 days. `status.py` is the one place Docker states become online, degraded, offline or unknown.
- **`watch/`**: `rules.py` holds the watch rules (restart loop, out-of-memory kill, sustained high CPU or memory, filling storage) as plain reads of stored data. `runner.py` evaluates them and runs the daily check. `investigate.py` asks the model to look into a signal using the agent's tools, and checks every log line it cites. `findings.py` stores findings and their evidence.
- **`ai/`**: `operator.py` builds a logpose agent for each chat answer and streams it. `tools.py` is the read-only tool set, `gate.py` the gate, `prompt.py` what the agent is told, `store.py` the AI settings and conversation list, `redact.py` the secret redaction.
- **`api/`**: one module per area (`host.py`, `containers.py`, `findings.py`, `agent.py`, `settings.py`). `payloads.py` builds the responses; the agent's tools reuse the same functions, so the agent sees what the screens show. `queries.py` holds the reads the routes share, `metrics.py` the time series for the charts (raw samples or roll-ups per range), and `security.py` the `X-Requested-With` header check for the POSTs that take no JSON body.
- **`auth/`**: the single admin account, argon2 password hashes, session tokens, sign-in lockout and `require_admin`.

## Running it locally

You need [uv](https://docs.astral.sh/uv/) and Node.js 24. Docker should be running, or the pages will have no container data: the backend still starts and records a capture error.

Start the backend (http://localhost:8000):

```sh
cd backend
uv sync
uv run uvicorn vegapunk.main:app --reload
```

In a second terminal, start the frontend dev server:

```sh
cd frontend
npm install
npm run dev
```

Open the address Vite prints (http://localhost:5173 by default). The dev server forwards every request under `/api` to the backend on port 8000 (see `frontend/vite.config.ts`), so the two behave like one site. Run one uvicorn worker only: the capture scheduler runs inside the process.

The backend reads Docker from `unix:///var/run/docker.sock` unless you set `VEGAPUNK_DOCKER_URL`. The other settings are listed in the [install guide](install.md#configuration).

## Tests, lint and build

These are the commands CI runs ([.github/workflows/ci.yml](../.github/workflows/ci.yml)).

Backend, from `backend/`:

```sh
uv sync --locked          # install exactly what uv.lock says
uv run ruff check .       # lint
uv run ruff format --check .   # formatting (use `uv run ruff format .` to fix)
uv run pytest --cov --cov-report=term-missing --cov-report=xml   # tests with coverage
```

Frontend, from `frontend/`:

```sh
npm ci                    # install exactly what package-lock.json says
npm run lint              # oxlint
npm run format:check      # Prettier (use `npm run format` to fix)
npm run test:coverage     # Vitest, once, with coverage
npm run build             # type-check and build into dist/
```

For a quick run without coverage, `uv run pytest` and `npm test` are fine. To run one backend test file, pass it to pytest: `uv run pytest tests/test_tools.py`. To run one frontend test file, pass it to Vitest: `npm test -- src/pages/Containers.test.tsx`.

Coverage has a floor, set a few points under the measured figure as a guard against regressions: `fail_under` in `backend/pyproject.toml` and `test.coverage.thresholds` in `frontend/vite.config.ts`. A run below it fails. CI shows the figures on the job's summary page and keeps the reports as artifacts. Locally, the frontend report is in `frontend/coverage/` (open `lcov-report/index.html`).

## The Docker image and the smoke test

The `Dockerfile` has three stages: it builds the frontend with Node, installs the backend with uv (in a stage that has `git`, because logpose is installed from a git tag), and copies both into a slim Python image that runs as user 10001.

From the repository root:

```sh
docker build .
docker compose config -q
docker compose -f compose.yaml -f compose.socket-proxy.yaml config -q
sh scripts/smoke.sh
```

`scripts/smoke.sh` builds the image, starts it under a throwaway Compose project on a free port, waits for Docker to report it healthy, requests `/api/health` and `/api/setup/status`, and always removes the container, network, volume and image it made. It does not touch a Vegapunk you already run (the container named `vegapunk`, port 8000 or its volume).

`docker compose up --build` from the root runs the whole app on port 8000, as a user would.

## The data directory and migrations

Everything Vegapunk keeps is in its data directory: `VEGAPUNK_DATA_DIR`, which is `data` (relative to where you start the server, so `backend/data` in development) and `/data` in the image. It holds:

- `vegapunk.db`: the admin account, sessions, captured data, findings, AI settings and the list of conversations.
- `conversations.db`: the content of each conversation, kept by logpose.
- `secret.key`: the key that encrypts the saved API key. Treat it as a secret and never commit it.

To start from scratch in development, stop the server and delete that folder.

The schema lives in numbered SQL files in `backend/vegapunk/migrations/`. At start, Vegapunk applies every file newer than the database's `PRAGMA user_version`, each in its own transaction, and refuses to start on a database written by a newer version.

To change the schema, **add a new file** with the next number (`0007_name.sql`). **Never edit a file that has shipped**: existing databases have already run it and will never run it again. `backend/tests/test_db.py` covers the migration rules.

## Common changes

### Add a collector

A new source of data is a new collector; nothing downstream should need to know about it.

1. If it needs to read something new from the host, add a **read** method in `backend/vegapunk/host/`. Never add a method that changes anything; `tests/test_host_docker.py` pins `DockerHost`'s public methods.
2. In `backend/vegapunk/capture/collectors.py`, write a class with a `name` and a `collect(now)` method that returns a list of records, following `HostCollector`. It should raise when the source can't be read; the scheduler records the error for that source and carries on with the others.
3. If the existing records in `capture/records.py` don't fit, add a new frozen dataclass there, add it to `Record`, store it in `capture/ingest.py` (keep it safe to repeat) and add a migration for its table.
4. Add the collector to `build_collectors`, and its name to `SOURCES` so its capture status is reported.
5. Test it with a fake in place of the host, as `tests/test_collectors.py` does.

### Add an agent tool

Tools are in `backend/vegapunk/ai/tools.py`. Each is a small typed function decorated with logpose's `@tool`, with a docstring the model reads (describe every parameter; a test checks this). A tool must:

- **only read.** The first version has read-only tools only. A tool that changes anything is a deliberate, reviewed step that belongs behind the planned permission levels; discuss it first.
- return something small (at most 8,000 characters) and come back with a short sentence instead of raising, so the model can explain a failure.
- pass anything from a container through `redact`, as a whole text.

Define it inside `build_tools()` and add it to the list it returns, in the same position as its name in `TOOL_NAMES` (a test asserts the two match), and update the pinned list in `tests/test_tools.py`. Every tool call passes through the single gate in `ai/gate.py`, which `operator.py` and `watch/investigate.py` build from `TOOL_NAMES`.

Adding a tool that writes is a deliberate, reviewed step, as [AGENTS.md](../AGENTS.md) says: it belongs behind the permission levels and needs agreement first. Any new tool means updating the tool count and list in [SECURITY.md](../SECURITY.md) and the tool count in AGENTS.md.

### Add an API route

Follow `backend/vegapunk/api/containers.py`, which is short. Routes live under `/api` and, apart from health, setup and sign-in, take the signed-in admin through `require_admin`. Put the response building in `api/payloads.py` when the agent's tools might want the same data. Register a new router in `app.py`. Test it through the app's test client, as `tests/test_api_containers.py` does, including that it refuses a request that isn't signed in.

### Add a frontend page

Follow `frontend/src/pages/Containers.tsx` and its test `Containers.test.tsx`. Pages load data with `usePolling` or `api()` from `src/api.ts`, build on the shared components in `src/components/`, and are routed in `src/App.tsx` (add a link in `components/SideNav.tsx` if it belongs in the navigation). Style with the tokens in `src/styles/tokens.css` and the classes in `components.css`; don't hard-code colours, spacing or fonts. In the interface, call the operator "Agent", not "AI".

## How the tests work

- **Backend tests never need Docker or a model.** `tests/conftest.py` builds the app with a fresh data directory for each test. The capture scheduler only runs when a test asks for it.
- **Fake Docker.** Tests pass a small fake in place of `DockerHost` or `SystemHost` (see `FakeDocker` in `tests/test_collectors.py` and `tests/agent_helpers.py`). `tests/helpers.py` has shortcuts for storing observations and samples.
- **A fake AI provider.** `tests/fake_openai.py` is a scriptable OpenAI-compatible server that runs over real HTTP and speaks the real streaming format, so the end-to-end tests (`test_agent_e2e.py`, `test_findings_e2e.py`) exercise logpose's real code path. `tests/agent_helpers.py` has a lighter in-process fake provider for unit tests.
- **Frontend tests** use Vitest and Testing Library with jsdom. `src/test-utils.tsx` stubs `fetch` with replies per route (`stubApi`), and `src/test-fixtures.ts` has sample data. A call a test hasn't stubbed fails loudly.

## Debugging

- **Server output.** In development, errors and capture failures are printed in the terminal running uvicorn. For the container, use `docker compose logs vegapunk`.
- **Look at the database.** The data directory's `vegapunk.db` is plain SQLite. Python 3.12 has a SQLite shell built in: `uv run python -m sqlite3 data/vegapunk.db` from `backend/`. `SELECT * FROM capture_status;` shows each source's last success and error.
- **Which version is running.** `uv run python -m vegapunk version` from `backend/`, or `docker exec vegapunk python -m vegapunk version` for the container.
- **Locked out while testing?** `uv run python -m vegapunk reset-password` sets a new admin password for the data directory in use and clears the sign-in pause.
- **Docker Desktop numbers look wrong.** On Docker Desktop, the host figures are the Docker VM's, not your machine's ([known issues](known-issues.md) 1). Container figures are real.

## Good places to start

These are open items in [known-issues.md](known-issues.md) that are well defined. Comment on or open an issue before you start, so nobody duplicates the work.

- **Verify on a real Linux host** (known issue 2). Install with the `DOCKER_GID` line, check the Overview's figures against the machine, and report what you find. No code needed.
- **Make the watch thresholds tunable** (known issue 7). They are constants in `backend/vegapunk/watch/rules.py`. This needs a short design discussion first (which settings, where in Settings).
- **Try other models and providers** (known issues 6 and 8): a hosted provider or Ollama, for chat and for findings. Report how they handle the tools and the findings' JSON schema.
- **Note on the Overview for Docker Desktop** (known issue 1): a small note when the host's OS is Docker Desktop. It is awaiting a decision, so propose wording in an issue first.
- **Check the pages in a browser** (known issue 3): the drawer, charts at phone width, and keyboard use of the account menu and chart.
- **The backend test warning** (known issue 5): the deprecation warning from Starlette's test client in every run.
