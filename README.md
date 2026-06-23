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
- **Auto-suggestions** from history — accept with → or `End`.
- **Multi-line input** via `Esc`-`Enter` or `Ctrl-J`, plus Emacs-style line editing.
- **Whole-line commands:** `exit`/`quit` to leave; `reset`/`clear` to wipe the conversation
  (approval decisions are kept).
- Tool activity is traced to **stderr** (`[think]` = a model round-trip, `[reason]` = the model's
  chain-of-thought when the model returns one, `[tool]` = a tool result), leaving **stdout** clean
  for the agent's replies.

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
| `VEGAPUNK_HISTORY_FILE` | REPL input-history file | `.vegapunk/history` |

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
  loop.py        # the agent loop: think → act (run tools) → observe → repeat
  session.py     # conversation state across turns
  brain.py       # LLM backend (local model via the OpenAI SDK)
  prompter.py    # prompt_toolkit input (history, suggestions, multi-line)
  approval.py    # interactive approval gate for guarded tools
  config.py      # settings + the persona system prompt
  tools/         # one module per tool, plus the @tool registry
tests/           # test suite
```
