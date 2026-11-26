# Vegapunk first-version roadmap

The first version (scope in [`AGENTS.md`](../../AGENTS.md)) is built in five milestones. Each one ends with software you can run and use, and each gets its own detailed plan in this folder, written when the milestone starts so it can build on what the previous one actually produced.

| # | Milestone | You can do this when it ships | Plan |
|---|---|---|---|
| 1 | Foundation | Run Vegapunk as a container, create the admin account, sign in, edit your profile | [2026-11-26-m1-foundation.md](2026-11-26-m1-foundation.md) |
| 2 | Capture and read-only screens | See the host's CPU, memory, storage and every container, with trends | To write |
| 3 | AI provider and agent chat | Connect a provider and ask the agent about the host | To write |
| 4 | Findings | Get findings from scheduled and background checks, with diagnosis and evidence | To write |
| 5 | Release | Install a published image by following the README | To write |

## Milestone 2: Capture and read-only screens

- **Host access module** (`backend/vegapunk/host/`): the only code that talks to Docker or the host, exposing read operations only.
- **Collectors and scheduler:** collectors produce normalized records; one ingest function stores them; an in-process scheduler runs them on an interval.
- **Storage:** raw samples kept for a short window, rolled up into coarser samples for trends.
- **API and screens:** Overview (the host), Containers, Container detail, plus their empty and error states.

Decisions to confirm when this plan is written:

| Decision | Proposal |
|---|---|
| Docker access | The `docker` Python SDK over the mounted socket |
| Host metrics from inside a container | `psutil` reading the host's `/proc` and `/sys`, mounted read-only |
| Storage used | Disk usage of the host filesystems, read through a read-only mount of the host root |
| Capture interval | 15 seconds, configurable in Settings |
| Retention | Raw samples for 24 hours, 5-minute averages for 30 days |
| Live updates in the UI | Poll the API every 5 seconds |

## Milestone 3: AI provider and agent chat

- **Provider settings:** base URL, API key, and model for any OpenAI-compatible endpoint, with a "Test key" check and model listing through logpose.
- **Key storage:** the API key is encrypted at rest with a key file in the data directory.
- **Operator:** a logpose `Agent` with read-only tools (container status, live container logs, stored metrics) and one gate (`on_tool_call`) that every tool call passes through.
- **Chat:** streamed replies over server-sent events, showing the steps the agent took; conversations stored as logpose sessions.
- **Screens:** Setup step 2 (AI provider), Settings, Agent chat, History, and the "AI is off" and "key rejected" states.

Decisions to confirm: `cryptography` (Fernet) for key encryption; secrets redaction rules for log lines sent to the provider; a cap on log lines per tool call.

## Milestone 4: Findings

- **Checks:** a scheduled health check (default daily) and background watching.
- **Findings:** each has a status, a diagnosis, the evidence lines it cites, and a suggested fix in words.
- **Screens:** Findings, Finding detail, "Ask agent about this finding", and "Needs attention" on the Overview.

Decision to confirm: background watching uses cheap rules (restart loops, out-of-memory kills, sustained high CPU or memory, filling disk) to decide when to call the model, so the provider is not called on every sample.

## Milestone 5: Release

- Publish the image to GitHub Container Registry on tagged releases.
- README install guide with a `compose.yaml`, upgrade notes, and `SECURITY.md`.
- Document the optional Docker socket proxy for a hard read-only guarantee.

## After the first version

Container management (start, stop, restart) and host commands for the agent, behind the permission levels (Observe only, Ask first, Auto-fix). These arrive as new tools behind the existing gate.
