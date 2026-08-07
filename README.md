# Vegapunk

**A local-first personal AI agent for your terminal.**

Vegapunk turns a language model into a persistent command-line assistant that can inspect your
workspace, use tools, remember useful context, resume conversations, and run recurring tasks. It is
built on [logpose](https://github.com/xdadwal/logpose), which provides the provider-neutral agent
loop beneath the terminal experience.

Use Vegapunk with Docker Model Runner for a fully local setup, or connect it to Anthropic, OpenAI,
Claude Code, Codex, and other providers supported by logpose.

```text
you -> Vegapunk REPL -> logpose Agent -> model provider
                              |
                              +-> workspace tools
                              +-> memory and sessions
                              +-> approval gate
                              +-> scheduled tasks
```

Vegapunk is under active development. Expect the interface and storage format to evolve while the
project matures.

## Highlights

- **Local-first by default.** Docker Model Runner keeps prompts, model inference, tools, and stored
  conversations on your machine. Network access occurs only when you select a hosted provider or
  the agent uses a web tool.
- **A terminal UI designed for long-running work.** Replies stream as rendered Markdown, including
  structured lists and fenced code, while reasoning summaries and tool activity stay visually
  distinct. Piped output automatically falls back to stable plain text.
- **Useful tools with explicit boundaries.** Vegapunk can read and search a workspace, fetch web
  content, edit files, and run commands. Side-effecting tools require interactive approval in
  manual mode, and all filesystem and shell access stays inside the configured workspace.
- **Persistent personal context.** Conversations, input history, and durable memories live in one
  local database. Sessions are auto-named and auto-saved after every successful turn.
- **Provider flexibility.** Switch backends and models without leaving the conversation. Supported
  providers expose readiness checks, model discovery, and configurable reasoning effort where
  available.
- **Reusable agent skills.** Drop any compatible [Agent Skills](https://agentskills.io) package into
  `.agents/skills/`; Vegapunk advertises it briefly and loads the full instructions only when needed.
- **Recurring tasks.** Schedule a prompt to run in a separate worker while Vegapunk is open. The
  worker is fail-closed and cannot use tools that require human approval.

## Requirements

- Python 3.10 or newer. Development and tests currently use Python 3.12.
- A supported model provider. The default setup expects Docker Model Runner at
  `http://localhost:12434/engines/v1` with `docker.io/gemma4:latest` available.

## Quickstart

Clone the repository and create a virtual environment:

```bash
git clone https://github.com/xdadwal/vegapunk.git
cd vegapunk
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

For the default local backend, enable Docker Model Runner and pull the configured model:

```bash
docker desktop enable model-runner --tcp 12434
docker model pull docker.io/gemma4:latest
```

Start Vegapunk from the directory you want it to treat as its workspace:

```bash
.venv/bin/python -m vegapunk
```

Manual approval is the default. To start a session in auto mode, where guarded tools run without
individual approval prompts, pass the explicit startup flag:

```bash
.venv/bin/python -m vegapunk --auto
```

Auto mode does not relax workspace confinement, and scheduled tasks remain fail-closed. The startup
banner displays a warning whenever auto is active.

Then ask for work in plain language:

```text
❯ Summarize this repository and identify the three highest-risk modules.
❯ Remember that I prefer pytest tests next to the behavior they cover.
❯ Check the latest release notes and save a concise migration guide.
```

The current model, session name, and context usage remain visible beneath the prompt. Use `/help`
at any time to see the local command surface.

## Terminal experience

Vegapunk selects its renderer based on the output stream:

- An interactive, color-capable terminal gets the Rich interface with streaming Markdown, compact
  tool traces, status indicators, and collapsed reasoning by default.
- Pipes, logs, tests, and non-interactive sessions get a plain renderer with stable line-oriented
  output. Assistant replies go to stdout; diagnostic and tool activity goes to stderr.
- `VEGAPUNK_UI=rich` or `VEGAPUNK_UI=plain` overrides automatic selection.

The prompt supports persistent history, inline suggestions, tab completion for commands and their
arguments, and arrow-key pickers for `/model`, `/sessions`, `/skill`, and `/effort`. Press
`Esc`-`Enter` or `Ctrl-J` to insert a newline; `Ctrl-D`, `/exit`, and `/quit` all end the session.

Press `Shift`-`Tab` to toggle approval mode without submitting or changing the current draft.
`manual` prompts before guarded tools run; `auto` allows them for the rest of the session until you
toggle back. The active mode is always written in the bottom toolbar and included in `/status`, so
the distinction remains visible with color disabled.

Reasoning is collapsed in the Rich interface by default. `/reason` shows the previous turn's
available reasoning summary, while `VEGAPUNK_REASONING=full` streams it into the live trace. Plain
mode retains the full trace for compatibility with scripts and logs.

## Commands

Lines beginning with `/` are handled by the REPL rather than sent to the model.

| Command | Description |
| --- | --- |
| `/help` | List available commands. |
| `/status` | Show backend readiness, model, effort, approval mode, context, session, scheduler, and workspace. |
| `/model [provider [model]]` | Show or switch the active provider and model. With no arguments, open the interactive picker. |
| `/effort [low\|medium\|high\|xhigh\|max]` | Show or change reasoning effort when the active model supports it. |
| `/sessions [name \| remove <name>]` | Pick or list recent sessions, resume one, or remove one. |
| `/save <name>` | Rename the current conversation. |
| `/history [n]` | Show the latest `n` turns; the default is five. |
| `/reason` | Show the reasoning supplied for the last completed turn. |
| `/skill <name>` | Include a skill's instructions with the next message. |
| `/schedule [list \| add <seconds> <prompt> \| remove <id>]` | Manage recurring prompts; the minimum interval is 60 seconds. |
| `/new` | Start a fresh conversation. Alias: `/reset`. |
| `/exit` | Quit Vegapunk. Alias: `/quit`; `Ctrl-D` also quits. |

## Model providers

`/model` reads the provider catalog from logpose, reports whether each backend is ready, and lets
you choose a model when discovery is supported. Common provider names are:

| Provider | Authentication | Notes |
| --- | --- | --- |
| `local` / `docker` | None | Docker Model Runner; this is the default. |
| `anthropic` | `ANTHROPIC_API_KEY` | Anthropic Messages API. |
| `openai` | `OPENAI_API_KEY` | OpenAI Responses API. |
| `openai-compat` | Server-dependent | OpenAI-compatible Chat Completions endpoint. |
| `claude` / `claude-code` | Local Claude Code session | Subscription-backed, unofficial integration. |
| `codex` | Local Codex session | Subscription-backed, unofficial integration. |

Select a provider at launch with `VEGAPUNK_PROVIDER`, or switch during a session:

```text
/model local docker.io/gemma4:latest
/model anthropic claude-sonnet-4-5
/model codex
```

Provider names correspond to credential types deliberately: `anthropic` and `openai` use API keys,
while `claude-code` and `codex` use existing CLI subscription sessions. The subscription backends
rely on undocumented authentication details and may stop working without notice; use the API-key
backends when you need a supported integration.

Switching models preserves the conversation. Reasoning state encoded by the previous provider is
removed when necessary so it is not replayed to an incompatible backend.

## Tools and approvals

Tools are ordinary type-hinted Python functions registered with `@tool`. The model receives their
generated schemas and can call them as part of a multi-step turn.

| Tool | Purpose | Approval |
| --- | --- | :---: |
| `get_time` | Return the current local date and time. | — |
| `get_battery` | Report battery charge and charging state. | — |
| `get_system_stats` | Report CPU, memory, disk, temperature, and uptime data. | — |
| `read_file` | Read a text file inside the workspace. | — |
| `list_dir` | List a directory inside the workspace. | — |
| `grep` | Search workspace content or filenames. | — |
| `write_file` | Create or overwrite a file. | Required |
| `edit_file` | Replace an exact snippet in an existing file. | Required |
| `run_shell` | Run a shell command in the workspace. | Required |
| `fetch_url` | Fetch a page and extract readable text. | — |
| `search_web` | Search the web through DuckDuckGo. | — |
| `remember` | Store a durable fact or preference. | — |
| `recall` | Search saved memories. | — |
| `use_skill` | Load a skill's full instructions. | — |
| `schedule_task` | Create a recurring prompt. | — |
| `yell` | Echo a response in uppercase. | — |

All file paths and shell commands are confined to `VEGAPUNK_WORKSPACE`, which defaults to the
directory where Vegapunk was launched. Before a guarded tool runs, an inline approval menu offers
four choices: allow once, deny, deny with guidance for the agent, or allow that tool for the rest
of the session. In auto mode those three guarded tools bypass the menu, while workspace confinement
continues to apply.

Tool results shown in the terminal are abbreviated for readability; the model receives output up
to `VEGAPUNK_OUTPUT_CAP` characters.

## Sessions, memory, and backups

Vegapunk stores sessions, durable memory, scheduled tasks, and REPL input history in a local Turso
database. The default path is `vegapunk.db` in the launch directory.

- A successful first turn is assigned a short model-generated session name, then saved after every
  turn. Use `/sessions` to resume or remove conversations and `/save` to rename the current one.
- Facts recorded through `remember` are added to future sessions automatically. If
  `VEGAPUNK_EMBED_MODEL` is configured, `recall` uses semantic similarity; otherwise it falls back
  to text matching.
- Startup creates a database snapshot when the newest backup is more than 24 hours old and retains
  the latest three files under `backups/`.
- One interactive Vegapunk process may use a database at a time. The scheduler worker has its own
  coordinated connection.
- The database is plaintext and SQLite-readable. Do not store secrets in conversations or memory.

Turso's multi-process WAL support is experimental and requires a local filesystem on 64-bit Linux
or macOS. Avoid placing the database on NFS or SMB storage.

## Scheduled tasks

Create a recurring task from the REPL or ask the model to schedule one:

```text
/schedule add 3600 Check the project status page and summarize any incident.
/schedule list
/schedule remove 8f17a2c4
```

The REPL starts a separate `vegapunk.scheduler_worker` process and stops it when you quit. Scheduled
runs never delay interactive input, and their trace is written to `scheduler.log` beside the
database. Logpose lifecycle metadata is kept out of the TUI: interactive runs append JSON Lines to
`vegapunk-runtime.jsonl`, and scheduled runs append them to `scheduler-runtime.jsonl`, also beside
the database. These content-free operational logs rotate at 5 MiB and retain three older files.

Unattended runs are fail-closed: tools that require approval (`write_file`, `edit_file`, and
`run_shell`) are refused because no person is present to approve them. The worker uses
`VEGAPUNK_SCHEDULER_MODEL`, falling back to the provider selected at startup; live `/model` changes
do not silently change the scheduled-task provider. Interactive auto mode never carries into the
scheduler worker.

## Skills

Vegapunk supports the community [Agent Skills](https://agentskills.io) format. Each skill is a
directory containing `SKILL.md` and, optionally, scripts, references, or assets:

```text
.agents/skills/
└── commit-message/
    ├── SKILL.md
    └── references/
```

At startup, only each skill's name and description enter the system prompt. The model calls
`use_skill` to load relevant instructions on demand, or you can force the next turn to use one with
`/skill <name>`. This progressive-disclosure model keeps the base prompt small while allowing
substantial reusable workflows.

Skills are rediscovered when used, but the short catalog advertised to the model is built at
startup. Restart Vegapunk after adding a skill if you want the model to discover it automatically.

## Configuration

Every application setting can be overridden with an environment variable.

### Core runtime

| Variable | Default | Purpose |
| --- | --- | --- |
| `VEGAPUNK_PROVIDER` | `local` | Provider selected at launch. |
| `VEGAPUNK_BASE_URL` | `http://localhost:12434/engines/v1` | Endpoint for Docker Model Runner or `openai-compat`. |
| `VEGAPUNK_MODEL` | `docker.io/gemma4:latest` | Model used by Chat Completions-compatible backends. |
| `VEGAPUNK_API_KEY` | `not-needed` | API key passed to the optional local embeddings client. |
| `VEGAPUNK_WORKSPACE` | Current directory | Root available to filesystem and shell tools. |
| `VEGAPUNK_MAX_OUTPUT_TOKENS` | `16000` | Maximum model output per turn, including reasoning. |
| `VEGAPUNK_MAX_STEPS` | `25` | Maximum think-act-observe iterations per turn. |
| `VEGAPUNK_PROVIDER_MAX_ATTEMPTS` | `3` | Total provider attempts for a turn; `1` disables retries. |
| `VEGAPUNK_PROVIDER_TURN_TIMEOUT` | Provider default | Complete provider-turn deadline in seconds; `0` disables it. |
| `VEGAPUNK_MAX_CONCURRENT_TOOLS` | `8` | Concurrent tool handlers allowed per agent. |
| `VEGAPUNK_TOOL_TIMEOUT` | `300` | Tool-handler deadline in seconds; `0` disables it. |
| `VEGAPUNK_SHELL_TIMEOUT` | `30` | Shell command timeout in seconds. |
| `VEGAPUNK_OUTPUT_CAP` | `10000` | Maximum tool-output characters returned to the model. |

### Interface

| Variable | Default | Purpose |
| --- | --- | --- |
| `VEGAPUNK_UI` | `auto` | Renderer: `auto`, `rich`, or `plain`. |
| `VEGAPUNK_COLOR` | `auto` | Color mode: `auto`, `always`, or `never`. `NO_COLOR` is also honored. |
| `VEGAPUNK_REASONING` | `collapsed` | Rich reasoning mode: `collapsed` or `full`. |
| `VEGAPUNK_CONTEXT_WINDOW` | `131072` | Local model context size used by the prompt gauge; `0` means unknown. |

### Hosted providers and scheduler

| Variable | Default | Purpose |
| --- | --- | --- |
| `VEGAPUNK_CLAUDE_MODEL` | Empty | Model for Anthropic and Claude Code backends. |
| `VEGAPUNK_CLAUDE_CONTEXT_WINDOW` | `200000` | Context size used by the Claude prompt gauge. |
| `VEGAPUNK_CLAUDE_EFFORT` | Empty | Initial Claude effort: `low`, `medium`, `high`, `xhigh`, or `max`. |
| `VEGAPUNK_CODEX_MODEL` | Empty | Model for Codex and OpenAI Responses backends. |
| `VEGAPUNK_CODEX_CONTEXT_WINDOW` | `0` | Context size used by their prompt gauge; `0` means unknown. |
| `VEGAPUNK_CODEX_EFFORT` | Empty | Initial Codex/OpenAI reasoning effort. |
| `VEGAPUNK_SCHEDULER_MODEL` | Empty | Scheduler provider and optional model as `provider[:model]`; empty inherits startup configuration. |
| `VEGAPUNK_SCHEDULER_EFFORT` | Empty | Scheduler effort; empty inherits the configured Claude effort. |

### Persistence and skills

| Variable | Default | Purpose |
| --- | --- | --- |
| `VEGAPUNK_DB_FILE` | `./vegapunk.db` | Database path. |
| `VEGAPUNK_EMBED_MODEL` | Empty | Embedding model used for semantic memory search. |
| `VEGAPUNK_SKILLS_DIR` | `./.agents/skills` | Agent Skills directory. |

## Development

Install the development dependencies and run the test suite:

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

The repository is intentionally small. The main extension points are:

```text
vegapunk/
├── cli.py               # interactive loop and command dispatch
├── commands.py          # slash-command registry and handlers
├── backend.py           # provider selection, models, and effort
├── session.py           # multi-turn conversation state
├── loop.py              # logpose event stream and agent loop integration
├── render.py            # Rich and plain terminal renderers
├── approval.py          # interactive approval UI
├── db.py                # Turso schema, locking, and backups
├── scheduler_worker.py  # recurring-task worker process
├── skills.py            # Agent Skills discovery and loading
└── tools/               # built-in tool implementations and registry
```

Contributions are welcome. Keep changes focused, add tests for behavioral changes, and ensure the
full suite passes before opening a pull request.
