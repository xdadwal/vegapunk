# Vegapunk first-version roadmap

The first version (scope in [`AGENTS.md`](../../AGENTS.md)) is built in five milestones. Each one ends with software you can run and use, and each gets its own detailed plan in this folder, written when the milestone starts so it can build on what the previous one actually produced.

| # | Milestone | You can do this when it ships | Plan |
|---|---|---|---|
| 1 | Foundation | Run Vegapunk as a container, create the admin account, sign in, edit your profile | [2026-11-26-m1-foundation.md](2026-11-26-m1-foundation.md) |
| 2 | Capture and read-only screens | See the host's CPU, memory, storage and every container, with trends | [2026-11-26-m2-capture.md](2026-11-26-m2-capture.md) |
| 3 | AI provider and agent chat | Connect a provider and ask the agent about the host | [2026-11-26-m3-agent.md](2026-11-26-m3-agent.md) |
| 4 | Findings | Get findings from scheduled and background checks, with diagnosis and evidence | To write |
| 5 | Release | Install a published image by following the README | To write |

## Milestone 2: Capture and read-only screens

- **Host access module** (`backend/vegapunk/host/`): the only code that talks to Docker or the host, exposing read operations only.
- **Collectors and scheduler:** collectors produce normalized records; one ingest function stores them; an in-process scheduler runs them on an interval.
- **Storage:** raw samples kept for a short window, rolled up into coarser samples for trends.
- **API and screens:** Overview (the host), Containers, Container detail, plus their empty and error states.

The decisions for this milestone are recorded at the top of its plan.

## Milestone 3: AI provider and agent chat

- **Provider settings:** base URL, API key, and model for any OpenAI-compatible endpoint, with a "Test key" check and model listing through logpose.
- **Key storage:** the API key is encrypted at rest with a key file in the data directory.
- **Operator:** a logpose `Agent` with read-only tools (container status, live container logs, stored metrics) and one gate (`on_tool_call`) that every tool call passes through.
- **Chat:** replies streamed to the browser as JSON lines, showing the steps the agent took; conversations stored as logpose sessions.
- **Screens:** Setup step 2 (AI provider), Settings, Agent chat, History, and the "AI is off" and "key rejected" states.

The decisions for this milestone are recorded at the top of its plan.

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
