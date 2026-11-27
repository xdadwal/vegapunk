# Contributing to Vegapunk

Thanks for taking a look. Vegapunk is a small, young project: version 0.1.0, pre-release, with one maintainer working on it in spare time. That means your help makes a real difference, and also that reviews can take a few days. This guide tells you what is useful, what the project will and won't take on yet, and what a pull request needs to get merged.

Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to help

You don't have to write code to help.

- **Run it on Linux and tell us how it went.** Linux is the supported target, but so far Vegapunk has only been run on Docker Desktop for macOS. The `DOCKER_GID` install path, the folder mount and the host figures on a real Linux machine are all unverified (see [known issues](docs/known-issues.md) 2). A short report of what worked and what didn't is one of the most valuable things you can send. Windows and other setups (Podman, a NAS, a Raspberry Pi) are welcome too.
- **Try it with your AI provider.** Only one local model has been tried. How a hosted provider or Ollama behaves with the agent and findings is unchecked (known issues 6 and 8).
- **Report bugs.** Use the bug form under [Issues](https://github.com/xdadwal/vegapunk/issues/new/choose).
- **Improve the docs.** If a step in the [install guide](docs/install.md) confused you, it will confuse the next person. Fixes to wording, missing steps and wrong commands are all welcome.
- **Add tests** for behaviour that isn't covered yet.
- **Fix small things**: typos, error messages, small bugs, items in [known issues](docs/known-issues.md).
- **Suggest ideas** in [Discussions](https://github.com/xdadwal/vegapunk/discussions).
- **Triage**: reproduce reported bugs, ask for missing details, point out duplicates.

[docs/development.md](docs/development.md) ends with a list of good places to start.

## Before you start

For anything beyond a small fix, open an issue (or find an existing one) first and say what you plan to do. That avoids two people doing the same work, and avoids you spending an evening on something that doesn't fit the first version.

### What the first version is

The scope is deliberately narrow. Its full text is in [AGENTS.md](AGENTS.md#first-version-scope); in short:

- **Observer mode only.** Vegapunk reads and reports. It changes nothing on the host: no starting, stopping or restarting containers.
- **Docker containers only.** Services are Docker containers; nothing else is managed.
- **One admin account.** No other users or roles.
- **One host.** Vegapunk runs on the host it watches and watches only that host. There is no separate host agent.
- **The agent only reads.** It raises findings and answers questions, and it may suggest a fix in words, but it never carries one out.

**Planned for later**, so please don't design it out: managing containers (start, stop, restart), and letting the agent run commands on the host to debug and act, gated by a permission level (observe only, ask first, auto-fix).

**Out of scope for now:** multiple hosts, a host agent, multiple users, non-Docker services.

A feature outside the first version needs a discussion before any code. Start one in [Discussions](https://github.com/xdadwal/vegapunk/discussions) or open a feature request, and wait for a reply before you build it.

## Development setup

See [docs/development.md](docs/development.md) for the repository layout, running the backend and frontend dev servers, tests, the Docker build and how the main parts fit together.

The short version: you need [uv](https://docs.astral.sh/uv/), Node.js 24 and, for the image, Docker with Compose. Docker should be running while you work, or the pages will have no container data.

### Your first pull request

1. Fork the repository on GitHub and clone your fork.
2. Create a branch for your change:

   ```sh
   git checkout -b fix-container-page-typo
   ```

3. Make the change, with tests.
4. Run the [checks](#checks-to-run-before-you-open-a-pull-request) below.
5. Push the branch to your fork and open a pull request against `main`. The template will ask a few questions; answer them briefly.

Not sure about something? Open the pull request as a draft and ask in it. Half-finished work with a question is fine.

## Checks to run before you open a pull request

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs three jobs on every pull request, and they are expected to pass. Run the same commands locally before you push.

**Backend**, from `backend/`:

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Run `uv run ruff format .` to fix formatting.

**Frontend**, from `frontend/`:

```sh
npm ci
npm run lint
npm test
npm run build
```

**Docker**, from the repository root:

```sh
docker build .
docker compose config -q
docker compose -f compose.yaml -f compose.socket-proxy.yaml config -q
sh scripts/smoke.sh
```

`scripts/smoke.sh` builds the image, starts it under a throwaway Compose project on a free port, checks that it becomes healthy, and removes everything it created. It does not touch a Vegapunk you already run.

## Coding conventions

The full set is in [AGENTS.md](AGENTS.md#conventions). The ones that come up most:

- **Write tests alongside new code.** Backend tests live in `backend/tests/`; frontend tests sit next to the file they test (`Thing.test.tsx`).
- **Keep changes small and simple.** Prefer a plain solution over a new abstraction.
- **Frontend styling uses the design tokens** in `frontend/src/styles/tokens.css` and the component styles in `components.css`. Don't hard-code colours, spacing or fonts.
- **Say "Agent", not "AI"**, for the operator in the interface. "AI" is only for the provider settings.
- **Migrations are never edited once shipped.** A schema change is a new numbered file in `backend/vegapunk/migrations/` (`0007_name.sql`).
- **Only `backend/vegapunk/host/` talks to Docker or the system**, and it exposes reads only. Everything else works from stored records.
- **Text from containers goes through `redact`** before it can reach the AI provider, as a whole text, and is always treated as data, never as instructions.
- **Agent tools are read-only**, and the list is pinned by a test. Adding a tool that changes anything is a deliberate, reviewed step.
- **No new dependency, language or framework without discussion.** Ask in the issue first.
- **Security is a first-class concern.** Vegapunk runs on people's machines with access to their Docker socket. Validate inputs, never run unvalidated commands, and never log or return secrets.

## Commits and pull requests

- **Keep pull requests small and focused.** One change per pull request is much easier to review than five.
- **Use a descriptive title** that says what the change does, for example "Show restart count on the container page".
- **Link the issue** it addresses ("Fixes #12").
- **Fill in the pull request template**: what and why, how you tested it, and the checklist (tests, checks passing, docs, tokens, dependencies, migrations, secrets and redaction, scope).
- **Update the docs in the same change** when behaviour changes, including [CHANGELOG.md](CHANGELOG.md) under `Unreleased` for anything a user would notice, and [docs/known-issues.md](docs/known-issues.md) when you fix or find an item there.

### Review and merging

`main` is protected. Every pull request needs an approving review from the maintainer (the code owner) before it can be merged. CI runs on every pull request and is expected to pass. Pull requests are merged with a merge commit, so your commits are kept as they are; tidy them up before asking for review if you like, but there's no need to squash. Force pushes to `main` are not allowed.

Expect questions and change requests; they're part of the process, not a judgement on you. If a pull request has gone quiet for a week, a friendly ping is fine.

## AI-assisted contributions

Using AI coding tools is fine. What matters is that **you understand every line you submit and have run it**: the tests, the checks above, and the feature itself where that's possible. Please don't submit changes you couldn't explain in review.

[AGENTS.md](AGENTS.md) is the guide for coding agents (Claude Code, Codex and others). Point your agent at it; it holds the scope, architecture and conventions.

## Licensing

Vegapunk is released under the [MIT License](LICENSE). By contributing, you agree that your contribution is released under the same licence. There is no contributor licence agreement, and you don't need to sign off your commits.

## Security issues

Please **don't** report a vulnerability in a public issue, pull request or discussion. Follow [SECURITY.md](SECURITY.md) and report it privately through GitHub.

## Questions

Ask in [Discussions](https://github.com/xdadwal/vegapunk/discussions). [SUPPORT.md](SUPPORT.md) lists where to go for what.

## Recognition

Contributors may be credited in [CHANGELOG.md](CHANGELOG.md): an entry for a change you made ends with "(thanks @username)". Add it to your own entry under `Unreleased` if you like. Thank you for helping.
