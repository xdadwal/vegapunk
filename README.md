# vegapunk

A self-hosted, CLI-first personal agent powered by a **local** LLM. Vegapunk runs a hand-built
agentic loop: it sends your input plus the available tool schemas to the model, runs whatever tools
the model calls, feeds the results back, and repeats until the model produces a final answer.
Irreversible actions (writing files, running shell commands) go behind an interactive approval gate.
When a request is underspecified, Vegapunk asks a short clarifying question rather than guessing, then
continues once you answer.

The model is served locally over an OpenAI-compatible API — by default
[Docker Model Runner](https://docs.docker.com/desktop/features/model-runner/) at
`http://localhost:12434/engines/v1`, running `ai/qwen2.5:latest`. The model and your files stay
local; the only outbound traffic is the `fetch_url` / `search_web` tools, when the agent uses them.

## Requirements

- **Python 3.10+** (developed and tested on 3.12).
- A reachable **OpenAI-compatible model endpoint**. By default Vegapunk targets Docker Model
  Runner; point it elsewhere with the environment variables below.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt        # runtime deps
.venv/bin/pip install -r requirements-dev.txt     # + pytest/ipdb, to run the tests
```

## Run

```bash
.venv/bin/python -m vegapunk
```

This starts an interactive REPL (it needs the model endpoint to be reachable). The REPL offers:

- **Persistent history** across sessions (`.vegapunk/history`), recalled with ↑/↓.
- **Persistent memory** — durable facts and preferences you share are saved to `.vegapunk/memory.md`
  and auto-loaded into future sessions, so Vegapunk still knows them next time.
- **Auto-saved conversations** — every chat is saved each turn under a short name the model picks
  from your first message (`.vegapunk/sessions/`), so you can pick it back up later.
- **Slash commands** (see below) — anything else you type goes to the model.
- **Auto-suggestions** from history — accept with → or `End`.
- **Multi-line input** via `Esc`-`Enter` or `Ctrl-J`, plus Emacs-style line editing.
- **Streaming output** — replies print token by token as the model generates them, instead of
  appearing whole after a long silence; a spinner marks the wait before the first token.
- Tool activity is traced to **stderr** (`[think]` = a model round-trip, `[reason]` = the model's
  chain-of-thought, streamed live as it's generated, `[tool]` = a tool result, truncated for
  display at 200 chars, `[note]` = a loop warning, e.g. the model ran out of tokens mid-answer),
  leaving **stdout** clean for the agent's replies.
- **Color-coded output**, themed on the Doctor himself: reasoning murmurs in Punk Records magenta,
  tools glow Egghead cyan, failures go Atlas red, warnings York yellow, and your prompt wears
  Shaka gold. Auto-disabled when a stream isn't a terminal; `NO_COLOR` and `VEGAPUNK_COLOR` give
  manual control.
- A **status toolbar** under the prompt shows the model and the current conversation's name on
  the left, and — after the first turn — how full the model's context window is on the right
  (exact server-reported tokens, absolute and percent).

### Commands

Lines starting with `/` are handled locally instead of being sent to the model:

| Command | What it does |
|---------|--------------|
| `/help` | List the available commands |
| `/history [n]` | Show the last `n` turns of this conversation (default 5) |
| `/sessions` | List saved conversations and their turn counts |
| `/save <name>` | Rename the current conversation |
| `/load <name>` | Resume a saved conversation |
| `/new` | Start a fresh conversation (aliases: `/reset`, `/clear`) |
| `/exit` | Quit (alias: `/quit`; `Ctrl-D` also quits) |

## Tools

Tools are type-hinted Python functions decorated with `@tool` (see `vegapunk/tools/registry.py`);
the decorator derives the name, description, and input schema and auto-registers them. The default
toolset:

| Tool | What it does | Approval |
|------|--------------|:--------:|
| `get_battery` | Report battery charge % and whether it's charging | — |
| `get_time` | Return the current local date and time | — |
| `read_file` | Read a file's full text (relative to the workspace) | — |
| `list_dir` | List the entries in a workspace directory | — |
| `grep` | Search the workspace by file contents or by filename | — |
| `write_file` | Create or overwrite a whole workspace file | ✋ gated |
| `edit_file` | Replace an exact snippet in an existing file (targeted edit) | ✋ gated |
| `run_shell` | Run a shell command in the workspace | ✋ gated |
| `fetch_url` | Fetch a web page and return its readable text | — |
| `search_web` | Search the web (DuckDuckGo) for external information | — |
| `remember` | Save a durable fact/preference about you for future sessions | — |
| `yell` | Echo the reply in UPPERCASE (a persona tool) | — |

Filesystem and shell tools are **confined to the workspace root** (default: the directory you
launched Vegapunk in); paths outside it are refused. When the model calls a **gated** tool, an
inline menu prompts **Yes / No / No — tell Vegapunk what to do instead / Always allow this tool this
session** before anything runs. Declining with a message hands the model your steer (fed back as the
tool result), so a "no" can redirect it instead of dead-ending.

## Configuration

All settings have defaults in `vegapunk/config.py` and can be overridden with environment variables:

| Variable | Purpose | Default |
|----------|---------|---------|
| `VEGAPUNK_BASE_URL` | OpenAI-compatible model endpoint | `http://localhost:12434/engines/v1` |
| `VEGAPUNK_MODEL` | Model id (Docker Model Runner needs the `ai/` prefix) | `ai/qwen2.5:latest` |
| `VEGAPUNK_API_KEY` | API key (ignored by a local server) | `not-needed` |
| `VEGAPUNK_WORKSPACE` | Root directory the file/shell tools are sandboxed to | current directory |
| `VEGAPUNK_SHELL_TIMEOUT` | Max seconds a shell command may run | `30` |
| `VEGAPUNK_OUTPUT_CAP` | Max characters of tool output fed back to the model | `10000` |
| `VEGAPUNK_MAX_STEPS` | Max think→act→observe steps per turn before the agent stops | `8` |
| `VEGAPUNK_COLOR` | CLI color: `auto` (only on terminals), `always` (even piped — overrides `NO_COLOR`), or `never`; the `NO_COLOR` standard also disables it | `auto` |
| `VEGAPUNK_CONTEXT_WINDOW` | The model's context window (tokens), for the toolbar's fullness gauge — find yours with `docker model logs \| grep n_ctx`; `0` = unknown (gauge shows tokens without a %) | `131072` |
| `VEGAPUNK_HISTORY_FILE` | REPL input-history file | `.vegapunk/history` |
| `VEGAPUNK_MEMORY_FILE` | Long-term memory file (auto-loaded into the system prompt) | `.vegapunk/memory.md` |
| `VEGAPUNK_SESSIONS_DIR` | Directory for saved conversations (one JSON file each) | `.vegapunk/sessions` |

## Tests

```bash
.venv/bin/python -m pytest -q
```

`pytest.ini` sets `pythonpath = .` and `testpaths = tests`.

## Project layout

```
vegapunk/
  __main__.py    # `python -m vegapunk` entry → cli.main()
  cli.py         # interactive REPL and command dispatch
  commands.py    # slash commands (/help, /save, /load, /sessions, /new, /exit)
  session_store.py # save/list/resume conversations on disk
  loop.py        # the agent loop: think → act (run tools) → observe → repeat
  session.py     # conversation state across turns
  brain.py       # LLM backend (local model via the OpenAI SDK)
  prompter.py    # prompt_toolkit input (history, suggestions, multi-line)
  approval.py    # interactive approval gate for guarded tools
  config.py      # settings + the persona system prompt
  style.py       # ANSI color for the trace and replies (Vegapunk-themed palette)
  memory.py      # long-term memory store (auto-loaded into the system prompt)
  tools/         # one module per tool, plus the @tool registry
tests/           # test suite
```
