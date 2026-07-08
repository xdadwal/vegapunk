# vegapunk

A self-hosted, CLI-first personal agent powered by a **local** LLM. Vegapunk runs a hand-built
agentic loop: it sends your input plus the available tool schemas to the model, runs whatever tools
the model calls, feeds the results back, and repeats until the model produces a final answer.
Irreversible actions (writing files, running shell commands) go behind an interactive approval gate.
When a request is underspecified, Vegapunk asks a short clarifying question rather than guessing, then
continues once you answer.

The model is served locally over an OpenAI-compatible API — by default
[Docker Model Runner](https://docs.docker.com/desktop/features/model-runner/) at
`http://localhost:12434/engines/v1`, running `ai/qwen2.5:latest`. With this default `local`
provider, the model and your files stay local; the only outbound traffic is the `fetch_url` /
`search_web` tools, when the agent uses them. The optional `claude` provider trades that away
deliberately: it sends the conversation (including tool results) to Anthropic, billed to your
Claude subscription — see [The `claude` provider](#the-claude-provider).

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
- **Skills** — teach Vegapunk repeatable procedures by dropping markdown files in
  `.vegapunk/skills/`; each is advertised to the model as one line, and its full instructions load
  on demand when a task matches (or force one with `/skill <name>`). See [Skills](#skills).
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
| `/skills` | List available skills |
| `/skill <name>` | Stage a skill's instructions to ride along with your next message |
| `/save <name>` | Rename the current conversation |
| `/load <name>` | Resume a saved conversation |
| `/model [local\|claude]` | Show or switch the model mid-conversation |
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
| `use_skill` | Load a skill's full instructions when a task matches one | — |
| `yell` | Echo the reply in UPPERCASE (a persona tool) | — |

Filesystem and shell tools are **confined to the workspace root** (default: the directory you
launched Vegapunk in); paths outside it are refused. When the model calls a **gated** tool, an
inline menu prompts **Yes / No / No — tell Vegapunk what to do instead / Always allow this tool this
session** before anything runs. Declining with a message hands the model your steer (fed back as the
tool result), so a "no" can redirect it instead of dead-ending.

## Skills

Skills teach Vegapunk repeatable procedures — one markdown file per skill under
`.vegapunk/skills/`. The design is progressive disclosure: at startup every skill costs the system
prompt only a one-line `name — description` ad, and the full body enters the conversation only
when it's needed — either the model calls `use_skill` because your request matches a listed skill,
or you force one with `/skill <name>` (its instructions then ride along with your next message).

A skill's **name is its filename** (slugified: `My Skill.md` → `my-skill`). The description comes
from a minimal frontmatter block; without one, the first line of the file serves as the ad:

```markdown
---
description: How to write a commit message for this repo
---
# Commit messages

- Format: type(scope): summary — imperative mood, <= 72 chars.
- Types: feat, fix, refactor, test, docs, chore.
- The body explains why, not what.
```

Malformed files degrade loudly rather than vanish (an unclosed frontmatter fence or missing
description falls back to sensible defaults with a `[skills]` note on stderr); skipping is the
exception and always announced — empty files, unreadable ones, duplicate names, and names with no
usable characters. Skill bodies are capped at `VEGAPUNK_OUTPUT_CAP` characters like any
other tool output. Skills are discovered at each use, but the ads in the system prompt are
assembled once at launch — a skill added mid-session works via `use_skill` and `/skill`, but isn't
advertised to the model until the next start.

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
| `VEGAPUNK_SKILLS_DIR` | Directory of skill files (one `.md` each, advertised at startup) | `.vegapunk/skills` |
| `VEGAPUNK_PROVIDER` | Brain at launch: `local` (Docker Model Runner) or `claude` (Claude subscription); switch live with `/model` | `local` |
| `VEGAPUNK_CLAUDE_MODEL` | Claude model override (e.g. `sonnet`, `opus`); empty = the Claude Code account default | (empty) |
| `VEGAPUNK_CLAUDE_CONTEXT_WINDOW` | Claude's context window (tokens), for the toolbar gauge | `200000` |

### The `claude` provider

`/model claude` (or `VEGAPUNK_PROVIDER=claude`) runs turns on your Claude Pro/Max
**subscription** — no API key. It works by driving the Claude Code CLI (bundled inside
the `claude-agent-sdk` dependency) as a subprocess, which is the officially sanctioned
way to spend a subscription from a program; usage draws from the same rate limits as
your interactive Claude Code sessions. Auth comes from Claude Code itself: run
`claude /login` once on the machine, or set `CLAUDE_CODE_OAUTH_TOKEN` (create a
long-lived token with `claude setup-token`). Vegapunk stays in charge either way —
Claude Code's own tools, settings, skills, and MCP servers are all disabled; Claude
requests Vegapunk's tools through the same loop and approval gate as the local model.

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
  brain.py       # the swappable model layer: Brain ABC, local DMR backend, create_brain factory
  claude_brain.py # Claude subscription backend (via the bundled Claude Code CLI)
  prompter.py    # prompt_toolkit input (history, suggestions, multi-line)
  approval.py    # interactive approval gate for guarded tools
  config.py      # settings + the persona system prompt
  style.py       # ANSI color for the trace and replies (Vegapunk-themed palette)
  memory.py      # long-term memory store (auto-loaded into the system prompt)
  skills.py      # skill discovery + on-demand loading (.vegapunk/skills/)
  tools/         # one module per tool, plus the @tool registry
tests/           # test suite
```
