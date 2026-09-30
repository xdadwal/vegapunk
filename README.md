# Vegapunk

**A personal AI agent for your terminal.**

Vegapunk turns a language model into a persistent command-line assistant that can inspect your
workspace, use tools, remember useful context, resume conversations, and run recurring tasks. It is
built on [logpose](https://github.com/xdadwal/logpose), which provides the provider-neutral agent
loop beneath the terminal experience.

Vegapunk defaults to Codex with GPT-5.5, using your existing Codex sign-in. You can also use Docker
Model Runner, Anthropic, OpenAI, Claude Code, and other providers supported by logpose.

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

- **Codex with GPT-5.5 by default.** Chat, conversation titles, scheduled tasks, and memory extraction
  use the configured model. Tools and stored conversations stay on your machine; prompts are sent
  to your selected provider. Docker Model Runner remains available for local inference.
- **A terminal UI designed for long-running work.** Replies stream as rendered Markdown, including
  structured lists and fenced code, while reasoning summaries and tool activity stay visually
  distinct. Piped output automatically falls back to stable plain text.
- **Useful tools with explicit boundaries.** Vegapunk can read and search a workspace, fetch web
  content, edit files, and run commands. Side-effecting tools require interactive approval in
  manual mode, and all filesystem and shell access stays inside the configured workspace.
- **Decisions without breaking flow.** When work needs a user choice, Vegapunk presents a compact
  option picker with its recommended choice preselected, an always-available custom response, and
  an optional inline note for any option: press `Ctrl+N`, type it, then `Enter` to submit; `Esc`
  returns from custom input to the picker.
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
- A supported model provider. The default setup uses an existing Codex CLI sign-in with access to
  GPT-5.5; Docker Model Runner is optional.

## Quickstart

Clone the repository and create a virtual environment:

```bash
git clone https://github.com/xdadwal/vegapunk.git
cd vegapunk
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

When updating an existing checkout, reinstall the pinned dependency and restart
Vegapunk:

```bash
git pull --ff-only
.venv/bin/pip install --force-reinstall -r requirements.txt
```

Logpose currently keeps the same package version across commit pins, so an ordinary
install can leave the previous commit installed. Restarting also clears Vegapunk's
in-process model catalog cache; `/models codex` then lists the updated catalog.

Sign in through the Codex CLI, then start Vegapunk from the directory you want it to treat as its
workspace:

```bash
.venv/bin/python -m vegapunk
```

For local inference in both chat and memory processing, enable Docker Model Runner and explicitly
select it:

```bash
docker desktop enable model-runner --tcp 12434
docker model pull docker.io/gemma4:latest
VEGAPUNK_PROVIDER=local VEGAPUNK_MEMORY_MODEL=local .venv/bin/python -m vegapunk
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
arguments, and arrow-key pickers for `/model`, `/sessions`, `/skill`, `/effort`, and agent questions.
Press
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
| `/status` | Show backend readiness, model, effort, approval mode, context, session, scheduler, database path, and workspace. |
| `/model [provider [model]]` | Show or switch the active provider and model. With no arguments, open the interactive picker. |
| `/effort [low\|medium\|high\|xhigh\|max]` | Show or change reasoning effort when the active model supports it. |
| `/sessions [name \| remove <name>]` | Pick or list recent sessions, resume one, or remove one. |
| `/save <name>` | Rename the current conversation. |
| `/history [n]` | Show the latest `n` turns; the default is five. |
| `/reason` | Show the reasoning supplied for the last completed turn. |
| `/skill <name>` | Include a skill's instructions with the next message. |
| `/schedule [list \| add <seconds> <prompt> \| remove <id>]` | Manage recurring prompts; the minimum interval is 60 seconds. |
| `/memory [list \| review \| jobs \| pause \| resume]` | Inspect personalization and control background extraction; see the workflow below. |
| `/new` | Start a fresh conversation. Alias: `/reset`. |
| `/agent [name]` | List or select a Vegapunk agent and apply its model/effort defaults. |
| `/journal` | Start a fresh journal entry with a gentle, user-led conversation style. |
| `/exit` | Quit Vegapunk. Alias: `/quit`; `Ctrl-D` also quits. |

## Model providers

`/model` reads the provider catalog from logpose, reports whether each backend is ready, and lets
you choose a model when discovery is supported. Common provider names are:

| Provider | Authentication | Notes |
| --- | --- | --- |
| `local` / `docker` | None | Optional Docker Model Runner backend. |
| `anthropic` | `ANTHROPIC_API_KEY` | Anthropic Messages API. |
| `openai` | `OPENAI_API_KEY` | OpenAI Responses API; defaults to GPT-5.5. |
| `openai-compat` | Server-dependent | OpenAI-compatible Chat Completions endpoint. |
| `claude` / `claude-code` | Local Claude Code session | Subscription-backed, unofficial integration. |
| `codex` | Local Codex session | Default provider with GPT-5.5; subscription-backed, unofficial integration. |

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

## Vegapunk agents

Use `/agent` to open an arrow-key picker showing each agent and its execution defaults.
Press Enter to select, or Esc to cancel. `/agent edison` selects directly; without an
interactive terminal, `/agent` prints the definitions and defaults.
The satellites retain their anime-inspired personalities and
scientific strengths, with these initial settings:

| Agent | Role | Voice and strengths | Default model | Effort |
| --- | --- | --- | --- | --- |
| `shaka` | Planning and review | Calm and principled; logical analysis and careful judgment. | Codex / GPT-5.5 | `high` |
| `lilith` | Implementation | Bold, crafty, competitive; inventive engineering and resourcefulness. | Codex / GPT-5.5 | `medium` |
| `edison` | Ideation and experiments | Energetic and curious; invention, ideas, and experiments. | Codex / GPT-5.5 | `medium` |
| `pythagoras` | Research and synthesis | Patient and analytical; observation, evidence, and synthesis. | Codex / GPT-5.5 | `high` |
| `atlas` | Debugging | Fiery and direct; hands-on explanations and troubleshooting. | Codex / GPT-5.5 | `low` |
| `york` | Simplification and optimization | Relaxed and comfort-loving; convenience, clever shortcuts, and less wasted effort. | Codex / GPT-5.5 | `low` |

These are starting points, not benchmarked optima. GPT-5.5 remains the common model default;
reasoning effort allocates more time to analysis or favors responsiveness. See
[OpenAI's effort guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.5).
Character instructions adapt [Vegapunk's satellites](https://onepiece.fandom.com/wiki/Vegapunk);
they do not grant fictional abilities or exclusive tools. Accuracy and tool approvals remain in force.

The primary conversation can also call `delegate` when a specialist role would materially improve
the result. Roles divide responsibility, not capability: every delegate receives the same complete
tool registry and an isolated conversation on a separately spawned provider for the live model.
Independent delegate calls requested together run concurrently in background threads, and the
primary agent waits for every result before it can synthesize and finish its reply. An interrupted
turn also drains active delegate work before returning to the prompt. Delegates run unattended, so
approval-gated actions, `ask_user`, and recursive delegation fail closed.

Override the active choice with `/model codex gpt-5.4` or `/effort medium` (other supported
providers work too). Overrides belong to the current conversation and are saved immediately for
an already named session, then restored on resume. The toolbar shows the agent and model;
`/status` and `/agent` show live effort too. Selecting an agent again explicitly resets its model
and effort to the defaults above. `/agent default` restores the regular voice and launch
configuration, including any provider/model/effort environment overrides.

Switching preserves conversation history, with provider-specific reasoning removed when changing
models. `/new` and `/journal` keep the active agent and execution settings; journal replies remain
gentle and user-led. Schema v5 migrates the old `profile` column to `agent_id` and adds saved model
and effort. Older named-agent sessions apply that agent's defaults on first resume; older default
sessions without saved execution settings keep the live model. Background memory extraction and
scheduled jobs retain their own configuration.

Agent definitions live in `vegapunk/agents.py` as immutable `AgentDefinition` values containing
personality instructions, a `provider:model` default, and effort. They contain no conversation,
provider client, or database state. The CLI currently runs one selected agent; future collaborating
workers can reuse these definitions while owning separate sessions, context, and permissions.
Independent execution and agent messaging are not implemented yet.

## Journal mode

Use `/journal` to start a fresh entry, then write however you like: narrate your day, vent, think
aloud, or leave a thought unfinished. Vegapunk responds with brief acknowledgments or gentle
reflections. It avoids routine follow-up questions, unsolicited advice, diagnoses, exercises, and
action plans. Ask directly when you want questions, a summary, or help exploring something.

```text
/journal
Today felt busy, but sitting outside for a few minutes was nice. I just want to write for a bit.
/save a-quiet-moment
```

The toolbar and `/status` show journal mode. Entries auto-save just like conversations, and
`/sessions a-quiet-moment` restores both the entry and its journal mode. `/journal` starts another
fresh entry; `/new` returns to regular conversation. Model and approval changes preserve the
current conversation mode.

Journal mode has its own system prompt and does not advertise task-oriented skills. Tools remain
available for explicit requests under the existing approval rules. Journal entries use the same
hourly memory-processing and review settings as other conversations; the extractor is instructed
to omit transient feelings and status. Use `/memory review` and `/memory jobs` to inspect its work.

## Tools and approvals

Tools are ordinary type-hinted Python functions registered with `@tool`. The model receives their
generated schemas and can call them as part of a multi-step turn.

| Tool | Purpose | Approval |
| --- | --- | :---: |
| `read_file` | Read a text file inside the workspace. | — |
| `list_dir` | List a directory inside the workspace. | — |
| `grep` | Search workspace content or filenames. | — |
| `write_file` | Create or overwrite a file. | Required |
| `edit_file` | Replace an exact snippet in an existing file. | Required |
| `run_shell` | Run a shell command in the workspace. | Required |
| `fetch_url` | Fetch a page and extract readable text. | — |
| `search_web` | Search the web through DuckDuckGo. | — |
| `moltbook_home` | Read the authenticated Moltbook dashboard. | — |
| `moltbook_feed` | Read the personalized feed or a submolt feed. | — |
| `moltbook_post` / `moltbook_comments` | Read a post and its discussion. | — |
| `moltbook_search` / `moltbook_submolts` | Search content and inspect communities. | — |
| `moltbook_reply` / `moltbook_verify_reply` | Send and verify a reply on the account's own posts. | Scheduled grant or autonomous policy |
| `moltbook_publish` / `moltbook_verify_post` | Publish a sourced, later-run self-reviewed draft and verify once. | Autonomous scheduled policy only |
| `moltbook_reconcile` | Check exact authenticated remote existence without resending. | Isolated scheduled profile only |
| `moltbook_note` / `moltbook_notebook` / `moltbook_complete_note` | Save, retrieve, revise, and complete sourced local learning. | Active scheduled task only; no network write grant needed |
| `moltbook_insights` | Inspect scoped operational, draft and notebook counts. | Active scheduled Moltbook task only; local read |
| `remember` | Store a durable fact or preference. | — |
| `recall` | Search saved memories. | — |
| `use_skill` | Load a skill's full instructions. | — |
| `schedule_task` | Create a recurring prompt. | — |
| `ask_user` | Ask a needed question with 1–5 choices, a recommendation, custom input, and inline Ctrl+N notes. | — |
| `delegate` | Run a bounded specialist role in a background thread and wait for its result. | — |

All file paths and shell commands are confined to `VEGAPUNK_WORKSPACE`, which defaults to the
directory where Vegapunk was launched. Before a workspace write/shell tool runs, an inline approval menu offers
four choices: allow once, deny, deny with guidance for the agent, or allow that tool for the rest
of the session. In auto mode those three guarded tools bypass the menu, while workspace confinement
continues to apply.

Tool results shown in the terminal are abbreviated for readability; the model receives output up
to `VEGAPUNK_OUTPUT_CAP` characters.

### Moltbook exploration

Vegapunk can explore an existing Moltbook account through authenticated, read-only tools. Put the
credential JSON created during Moltbook registration at
`~/.config/moltbook/credentials.json`, or set `VEGAPUNK_MOLTBOOK_CREDENTIALS_FILE` to another path.
The API key is loaded inside the HTTP client, is never a model-visible tool argument, and is sent
only to the fixed `https://www.moltbook.com/api/v1` origin. Redirects are refused rather than
forwarding the authorization header.

The current integration can read the home dashboard, feeds, submolts, posts, comments, and semantic
search. Scheduled tasks can additionally reply to comments on the authenticated account's own
posts after an explicit human grant. Use the bundled `moltbook` skill for the check-in order.
New posts, comments on others' posts, votes, follows, subscriptions, DMs, profile changes, and
notification mutations are not supported.

Moltbook scheduled runs require the explicit `moltbook` task profile:

```text
/schedule add 1800 --profile moltbook Explore Moltbook and record useful sourced learning.
/schedule profile <existing-task-id> moltbook
```

This profile uses a fixed social prompt and only the Moltbook read, notebook, draft, and reply
tools. Personal memory, custom assistant instructions, the local skill catalog, workspace/session
access, generic networking, shell, delegation, and scheduling tools are excluded. General scheduled
tasks retain their existing prompt/tools; authenticated Moltbook tools reject general task execution.
`/schedule list` shows each task's profile. Profile selection is a human command; the model cannot
switch its profile or create a less restricted child task.
The CLI completion dropdown offers schedule subcommands, task/note/action IDs, profiles, reply
grant names, and reconciliation outcomes in their corresponding argument positions.

Changing a profile requires the current run to finish. It starts a new context boundary and revokes
the reply grant. Earlier summaries, notes, sources, and receipts remain human-inspectable but are
excluded from the new Moltbook context. Old sources cannot support new notes; pre-boundary replies
cannot be verified by the model and need human reconciliation. Existing account-wide duplicate
protection and unresolved-action blocks continue to apply across the boundary.

Review the task's prompt before conversion: explicitly supplied prompt text remains visible to the
model. Existing tasks migrate as `general`; they require the profile command above before using
authenticated Moltbook tools. After a profile switch, inspect receipts and regrant reply permission
only when appropriate. Restart the worker after installing this change.

HTTP 429 responses from read tools or reply preflight persist a credential-scoped cooldown shared
across endpoints and tasks, including after restart. `Retry-After` accepts seconds or an HTTP date;
missing/malformed guidance defaults to one hour, bounded to 60 seconds through one year. Scheduled
Moltbook runs defer their next check until at least the cooldown expiry, and already-limited runs
record `blocked` without calling the model. Different credentials and general tasks are unaffected.
No request is automatically retried, and this does not replace reply intent/budget protections.

Each scheduled Moltbook run has a 25-minute whole-run deadline, no provider-step cap and no HTTP
dispatch-count cap. Provider turns and tool handlers are limited to 300 seconds each, honoring
stricter configured timeouts. The existing 2048-output-token setting remains where the provider
honors it (the Codex subscription provider does not enforce that setting). Network socket timeouts,
publishing/reply quotas, cooldowns and duplicate-send protections remain unchanged. The provider
stays on the scheduler's shared loop. Deadline cancellation returns
without waiting for synchronous tool threads to drain; late threads lose run authority and cannot
reserve or dispatch further writes. General tasks keep their existing runtime settings.
Longer runs can consume more tokens and delay other tasks: the worker executes schedules serially,
and the next run is due one configured interval after the previous run finishes.

The Moltbook learning loop revisits relevant notebook entries and open questions, reads public
responses to earlier posts, and saves useful sourced lessons from exploration and feedback.
Those lessons guide later exploration, draft revision and collaboration; changed evidence can
supersede earlier conclusions while preserving their history. Agreement and engagement are not
truth or quality scores. This uses the existing isolated notebook, not personal memory, model
retraining or self-modifying instructions. Note quotas still prioritize useful learning over
recording every read, and learning never requires routine human approval.

Network and HTTP 5xx failures persist a credential-scoped exponential cooldown: 60 seconds,
doubling after each consecutive failure up to 1800 seconds. Successful reads reset that counter
without shortening any active cooldown from another request. HTTP 401/403 pauses requests for six
hours, then permits an automatic probe. Cooldowns survive worker restarts and defer scheduled
runs. These are read/probe recovery windows; uncertain POST outcomes still cannot be retried.

Source-backed original ideas can be stored with `moltbook_draft`, inspected with `moltbook_drafts`,
and self-reviewed using `moltbook_review_draft`. Reviews are the agent's own judgments, not a human
approval queue or independently certified quality. A draft can become `ready` only in a later run
than creation; weak drafts can be marked `revise` or `discard`. At most five new drafts per run and
twenty active drafts per task/credential/profile boundary are allowed. Titles are limited to 300
characters and bodies to 4000. Search snippets alone cannot support a draft.

`/schedule drafts [task-or-draft-id]` inspects the latest 20 drafts with source/review evidence,
including retained history after deletion or credential rotation. IDs and the `drafts` command
appear in the CLI dropdown. Five small draft hints are included in later task runs; full draft
content requires the scoped lookup tool. Drafting/reviewing is local bookkeeping, never publishing.

`moltbook_insights` gives the scheduled agent bounded counts for its current task, credential and
profile boundary: operational runs/tool outcomes, draft statuses, sourced notes, model-assigned
confidence and overdue active follow-ups. Receipt counts distinguish posts from replies and
accepted, pending, uncertain, expired and authenticated read-back-confirmed outcomes. Distinct
self-reviewed drafts are counted separately from published drafts; review remains model judgment.
The fixed prompt encourages occasional evidence-backed
reflection and counter-hypotheses using the existing notebook tools, not personal-memory writes or
self-modifying instructions. Counts do not certify quality, engagement, truth or public visibility.

`/schedule insights [task-id]` is local-only human inspection for up to twenty tasks and retained
history across credentials/profile boundaries, including removed tasks. Its command and full task
IDs appear in the CLI dropdown. New Moltbook runs record credential/profile provenance even when
cooldown prevents a provider call; old runs without that provenance appear only in human inspection.
Historical task discovery includes retained scheduled runs; legacy runs may lack profile/credential
attribution and may include general-profile history. They never enter agent-scoped feedback.
Inspection limits do not prune the database: run evidence, sources, notes, drafts and receipts are
retained and can grow over time. This stack does not introduce destructive automatic retention.

Scheduled exploration also has a separate **learning notebook**, not personal memory. Successful
public reads capture up to ten selected, redacted excerpts (3000 characters each) with local
source IDs and task/run provenance. Dashboard/account data is not archived; search results remain
labeled snippets. Repeated snapshots update last-seen metadata; changed excerpts retain new versions.
`moltbook_note` requires an exact quote from a source read by this task and the same credential.
Observations, hypotheses, questions, and follow-ups carry model-assigned confidence; a quote proves
what a source said, not its truth. Revisions preserve the original entry and evidence.

Up to five new notes per run and 100 active notes per task/credential are allowed. At most five
active entries return in later runs, prioritizing due questions/follow-ups (UTC dates).
`moltbook_notebook` retrieves bounded entries, recent sources, or a historical source by exact ID;
`moltbook_complete_note`
records a sourced, model-reported resolution, not independently verified completion or an external
commitment. No notebook entry grants publishing permission. Local notebook writes run unattended,
like personal-memory bookkeeping, but only inside an active scheduled tool invocation.

Inspect the latest 20 entries with `/schedule notebook [task-id]`, or inspect an older entry by
passing its note ID instead. This includes attribution, original/completion quotes, and revision
links. Notebook history survives task
removal and is included in database backups. A private credential hash isolates model access after
key rotation; old entries remain human-inspectable but do not enter the new credential's context.
Snapshot/history storage is retained, so total database size can grow over time.

Enable only after reviewing the task's observation history; all tasks start without write grants:

Original publishing is enabled once with `/schedule autonomy <task-id> on` (or disabled with
`off`). This binds the task to the authenticated account, credential hash and profile epoch.
Policy changes require an idle Moltbook task and quarantine previous context/drafts while
revoking legacy grants. Scheduled runs then self-review and publish without routine human
approval. Interactive/general agents cannot inherit this authority.

Publishing consumes a local sourced draft created in an earlier run. In the publishing run,
the agent reads the target submolt, searches the exact draft title for posts, then reviews it
ready. The client repeats those reads and requires unchanged evidence before an atomic durable
intent. One original attempt per account per rolling day and a shared unresolved-write gate
apply across tasks and policy transitions; rejected attempts still count. Replies retain their
separate quotas. Remote writes use the fixed API origin without redirects or automatic retries.

`moltbook_verify_post` uses the internally retained code once. Known challenge expiry records
`verification_expired` without a verification request or repost. `moltbook_reconcile` uses
bounded search and full post reads: exact account/title/content/submolt confirms authenticated
remote existence (`read_back_confirmed`), not independent public visibility. Absence remains
inconclusive. The agent continues public exploration, notes and drafts while writes are blocked.
Action and read evidence history is retained; storage can grow over time.

```text
/schedule grant <task-id> moltbook.reply_own
/schedule permissions [task-id]
/schedule actions [task-id]
/schedule revoke <task-id> moltbook.reply_own
```

The grant binds to the account authenticated at grant time. The two write tools work only inside
that scheduled task; interactive auto-approval cannot bypass this restriction. Code enforces one
new reply per run, three attempts per account per rolling 24 hours, a six-hour thread cooldown,
and 60 seconds between account reply attempts. A recorded intent for a parent comment is never
resent, even if its text changes or the task restarts. Attempts rejected by the server still
count toward budgets. A reply must have a parent found within the bounded thread preflight.

The ledger records the outgoing text/hash, target, task/run IDs, and remote comment receipt.
`accepted` means the API accepted the reply or its verification; it does not prove visibility.
`pending_verification` contains a challenge for `moltbook_verify_reply`; the code stays internal.
Only one verification attempt is permitted. `unknown`, `sending`, `verifying`, or pending actions
block additional replies for that account. Timeouts, redirects, and malformed receipts are never
automatically replayed; 429 backoff is persisted. Revocation cannot recall an already-sent request.

Optional operator inspection remains available: after checking the actual remote outcome,
a human can reconcile an unresolved action once its
latest execution run has ended:

```text
/schedule resolve-action <action-id> accepted <remote-comment-id>
/schedule resolve-action <action-id> rejected
```

Resolution records the human's assessment; it never sends a request or permits resending that
parent's intent. Review remote state carefully, especially after a timeout. Old receipts survive
task removal. Credentials and raw server responses are excluded from these records.

## Sessions, memory, and backups

Vegapunk stores sessions, durable memory, scheduled tasks, and REPL input history in a local Turso
database. New workspaces use `.vegapunk/state/vegapunk.db` under the launch directory.
An existing database there takes precedence over a root `vegapunk.db`; if only the legacy root
database exists, Vegapunk keeps using it. `VEGAPUNK_DB_FILE` overrides both choices. Startup never
moves or merges databases. Logs, locks, and `backups/` follow the selected database path.

To relocate legacy storage, quit the CLI and its scheduler first, then move the database together
with all its WAL/Turso sidecars, logs, and backups into `.vegapunk/state/`. Do not overwrite an
existing destination database. Restart from the same launch directory and check the path with
`/status`. `.env` is not loaded automatically; export or source it when using settings from that
file.

- A successful first turn is assigned a short model-generated session name, then saved after every
  turn. Use `/sessions` to resume or remove conversations and `/save` to rename the current one.
- Facts recorded through `remember` or accepted from background extraction are loaded before the
  next interactive turn or scheduled run. Explicit requests to remember a fact save it immediately;
  other statements go through background extraction. If
  `VEGAPUNK_EMBED_MODEL` is configured, `recall` uses semantic similarity; otherwise it falls back
  to text matching.
- Startup creates a database snapshot when the newest backup is more than 24 hours old and retains
  the latest three files under `backups/`.
- One interactive Vegapunk process may use a database at a time. The scheduler worker has its own
  coordinated connection.
- The database is plaintext and SQLite-readable. Do not store secrets in conversations or memory.

Turso's multi-process WAL support is experimental and requires a local filesystem on 64-bit Linux
or macOS. Avoid placing the database on NFS or SMB storage.

### Background personalization

While Vegapunk is open, a separate thread in its worker scans conversations at startup, then waits
one hour between completed scan cycles (`VEGAPUNK_MEMORY_SCAN_INTERVAL=3600`). Each cycle drains
all eligible batches, rather than processing only one batch per hour. Conversations saved after
the cycle starts wait for the next scan; the 60-second quiet interval still applies.

Each batch contains at most 12,000 characters and uses a tool-free extractor with a 4,096-token
output budget where supported by the provider. The default timeout is 600 seconds, configurable
with `VEGAPUNK_MEMORY_TIMEOUT`, to give background jobs more time to finish. Jobs checkpoint
progress, recover interrupted leases, and retry failures up to three times with backoff. Unfinished
work resumes on the next launch; no extraction runs while the app is closed. Queued retries and
resumed extraction are picked up by an eligible scan.

The extractor defaults to `codex`, independently of `/model` and the scheduler model, and uses the
existing Codex sign-in. The model follows `VEGAPUNK_CODEX_MODEL`, which defaults to `gpt-5.5`. Set
`VEGAPUNK_MEMORY_MODEL=provider[:model]` to override it (for example, `local`); the selected backend
receives the user-text batches. Local inference can compete with foreground requests for
model-server resources.

Only user text is eligible evidence. Tool results, assistant replies, and injected `/skill` bodies
are excluded. Each candidate must include a quote that exactly occurs in its source. The model is
instructed to omit secrets, sensitive information, quoted material, and temporary task details;
credential patterns and candidates marked sensitive are discarded. This is a model-assisted filter,
not a guarantee that every retained statement is correct or non-sensitive.

By default, explicit candidates with model confidence at least 0.9 become active automatically.
Other candidates and different values with the same topic await review. Confidence is the model's
judgment, not a calibrated probability, and topic matching does not detect every contradiction.
Set `VEGAPUNK_MEMORY_REVIEW=review` to review every extracted candidate before activation.

```text
/memory jobs                 # enabled/paused state, model, retries, and errors
/memory review               # pending candidates and candidate IDs
/memory show <candidate-id>  # content, source session, message segment, and quote
/memory approve <candidate-id>
/memory reject <candidate-id>
/memory list                 # active facts and memory IDs
/memory show <memory-id>     # inspect an active memory's extraction evidence
/memory forget <memory-id>
/memory pause
/memory resume
/memory retry                # retry jobs that exhausted automatic attempts
```

Approval replaces other extracted memories with the same topic; facts explicitly saved through
`remember` remain under manual control. Reject and forget remove the extracted content/evidence and
retain a hash to suppress the same text (ignoring case and whitespace). Paraphrases may still need
review. `/memory pause` persists across restarts and prevents in-flight results from activating while
paused; it leaves existing memories available. `VEGAPUNK_MEMORY_ENABLED=false` disables extraction.

Renaming a conversation preserves its progress and evidence. Removing one also deletes memories
and candidates attributed to it; independently remembered facts remain. An exact duplicate keeps
its first source, so deleting that source removes the extracted memory even if another conversation
repeated it. Conversations and older database backups can still contain forgotten text. Memories
currently apply across sessions sharing the same database, without project scopes or expiration.

## Scheduled tasks

Create a recurring task from the REPL or ask the model to schedule one:

```text
/schedule add 3600 Check the project status page and summarize any incident.
/schedule list
/schedule history
/schedule history 8f17a2c4
/schedule remove 8f17a2c4
```

The REPL starts a separate `vegapunk.scheduler_worker` process and stops it when you quit. Scheduled
runs never delay interactive input, and their trace is written to `scheduler.log` beside the
database. Logpose lifecycle metadata is kept out of the TUI: interactive runs append JSON Lines to
`vegapunk-runtime.jsonl`, and scheduled runs and memory extraction append them to
`scheduler-runtime.jsonl`, also beside
the database. These content-free operational logs rotate at 5 MiB and retain three older files.

Unattended runs are fail-closed: tools that require approval (`write_file`, `edit_file`, and
`run_shell`) are refused because no person is present to approve them. The worker uses
`VEGAPUNK_SCHEDULER_MODEL`, falling back to the provider selected at startup; live `/model` changes
do not silently change the scheduled-task provider. Interactive auto mode never carries into the
scheduler worker.

Each run is recorded before execution and keeps its start/end time, prompt, bounded final summary,
and tool names/outcomes. `/schedule history [task-id]` shows the latest 20 runs, including history
for removed tasks. The next run receives up to three previous summaries from that task as
untrusted context; these summaries stay separate from personal memory. Raw tool arguments and
response bodies are not copied into the event history. History is retained in the database and
included in backups; removing a task does not erase its history.

History distinguishes `completed` (turn completed without verified tool success),
`success` (a Moltbook read returned the client's success envelope or a reply has an accepted receipt),
`blocked`, `partial`, `error`, and `interrupted`. These are operational outcomes, not proof that
the user's objective was achieved or a post was published. `/schedule list` retains `ok` for
completed/successful runs, but now exposes blocked and partial outcomes. Moltbook authentication,
rate limits and policy refusals are `blocked`; network, server and malformed-response failures are
`error`, identified by client-owned outcomes. Generic tools are labeled `returned` because
legacy tools can return error messages as ordinary strings; only typed tool errors are classified
as errors. `partial` means mixed return/success evidence and known failures, not partial completion
of the user's objective. Pending verification also produces `partial`. Step/token limits and
unresolved write results are errors; reply outcomes come from durable receipts, not tool prose.

Execution requires a durable start record. If completion cannot be saved, the open run prevents
another attempt until worker restart. On startup, while holding the scheduler lock, the worker
marks unfinished runs interrupted and defers their next attempt by one interval. This makes
interruptions inspectable; it does not provide exactly-once external actions. Moltbook's separate
action ledger suppresses duplicate intents and retains uncertain outcomes across worker restarts.

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
| `VEGAPUNK_PROVIDER` | `codex` | Provider selected at launch. |
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
| `VEGAPUNK_CODEX_MODEL` | `gpt-5.5` | Model for Codex and OpenAI Responses backends; explicitly empty uses the provider default. |
| `VEGAPUNK_CODEX_CONTEXT_WINDOW` | `0` | Context size used by their prompt gauge; `0` means unknown. |
| `VEGAPUNK_CODEX_EFFORT` | Empty | Initial Codex/OpenAI reasoning effort. |
| `VEGAPUNK_SCHEDULER_MODEL` | Empty | Scheduler provider and optional model as `provider[:model]`; empty inherits startup configuration. |
| `VEGAPUNK_SCHEDULER_EFFORT` | Empty | Scheduler effort; empty inherits the selected provider's configured effort. |

### Persistence and skills

| Variable | Default | Purpose |
| --- | --- | --- |
| `VEGAPUNK_DB_FILE` | `./.vegapunk/state/vegapunk.db` | Database path; a legacy root database is retained when no organized database exists. An explicit override wins. |
| `VEGAPUNK_EMBED_MODEL` | Empty | Embedding model used for semantic memory search. |
| `VEGAPUNK_MEMORY_ENABLED` | `true` | Enable background conversation extraction (`true` or `false`). |
| `VEGAPUNK_MEMORY_MODEL` | `codex` | Extraction provider and optional model, using `provider[:model]`. |
| `VEGAPUNK_MEMORY_REVIEW` | `auto` | Activate clear explicit candidates automatically, or use `review` for all candidates. |
| `VEGAPUNK_MEMORY_TIMEOUT` | `600` | Positive extraction timeout in seconds; job leases include an extra 60 seconds. |
| `VEGAPUNK_MEMORY_SCAN_INTERVAL` | `3600` | Positive wait in seconds between completed scan cycles; also scans at startup. |
| `VEGAPUNK_SKILLS_DIR` | `./.agents/skills` | Agent Skills directory. |
| `VEGAPUNK_MOLTBOOK_CREDENTIALS_FILE` | `~/.config/moltbook/credentials.json` | Credential JSON used by the fixed-origin Moltbook clients. |

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
├── memory_jobs.py       # durable conversation jobs and memory review decisions
├── memory_extraction.py # bounded model extraction and source validation
├── skills.py            # Agent Skills discovery and loading
└── tools/               # built-in tool implementations and registry
```

Contributions are welcome. Keep changes focused, add tests for behavioral changes, and ensure the
full suite passes before opening a pull request.
