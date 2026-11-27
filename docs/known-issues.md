# Known issues

Things we know are wrong, unverified, or deliberately limited. Each has a status so nothing is lost between milestones. Fix or close an item by editing this file in the same change.

## Release status (0.1.0)

What has been run, and what has not:

- **Verified on Docker Desktop for macOS:** Vegapunk itself, with the agent and findings using Gemma (`docker.io/ai/gemma4:latest`) through Docker Model Runner; a fresh install, backup, restore and upgrade as written in the [install guide](install.md); and the [socket proxy](socket-proxy.md).
- **Not verified:** a Linux host, including the `DOCKER_GID` path (see 2); any hosted AI provider, and Ollama (see 6 and 8); the smoke test running in GitHub Actions; Windows.

## Open

### 1. Docker Desktop reports the Docker VM, not the machine

**Found:** 2026-11-26, running milestone 2 on macOS with Docker Desktop.

On Docker Desktop (Mac or Windows) containers run in a small Linux VM, so Vegapunk reports that VM:

| | Vegapunk showed | The Mac actually has |
|---|---|---|
| Host name | `docker-desktop` (kernel `linuxkit`) | the Mac's own name |
| Memory | 7.8 GiB (Docker Desktop's limit) | 16 GiB |
| Storage | 1007 GiB used 17 GiB (the VM's sparse disk image) | 460 GiB, 274 GiB used |
| Uptime | since the VM started | since the Mac booted |

CPU cores match only because Docker Desktop gives the VM every core. The **Storage figure is misleading** there: it is bigger than the real disk. Container data (names, states, CPU, memory per container) is real and matches `docker stats`.

On a Linux host the numbers are the machine's. That is the supported target, but it is **not yet verified on Linux** (see 2).

The [install guide](install.md#docker-desktop-shows-the-wrong-memory-storage-or-host-name) now says that Docker Desktop reports the VM and that Storage is not meaningful there. Still open: a small note on the Overview when the host's OS is `Docker Desktop` (not done, awaiting a decision).

### 2. Not verified on a real Linux host

Everything was run against Docker Desktop, where the Docker socket is open to every user. On Linux the socket belongs to the `docker` group, which is why compose uses `group_add: ${DOCKER_GID}`. That path, and the claim that a container's own `/proc` is host-wide, are untested on Linux.

### 3. The pages have not been looked at in a browser by the builder

Layout, charts at real widths and phone width are covered by tests only. The user has seen the Overview, but the drawer, charts on phones and keyboard use of the account menu and chart have not been checked by eye.

### 4. "Old values are hidden after 45 s" not observed live

Covered by unit tests. A live run did not wait long enough to see a stale container's values disappear.

### 5. Backend test warning

`httpx` is deprecated for Starlette's test client, which prints a warning in every backend test run. Switching to `httpx2` was attempted and blocked by the permission system.

### 6. The agent has been tried with one local model only

Milestone 3 was verified against a scripted fake OpenAI-compatible server over real HTTP, which exercises logpose's real code path. Since then it has been run with one local model, Gemma through Docker Model Runner. No hosted provider has been tried: how well other models use the tools, how long answers take, and how a given provider words its errors are unchecked.

### 7. Thresholds are not tunable

The watch rules' thresholds are constants in `backend/vegapunk/watch/rules.py`. There is no setting for them yet.

### 8. Findings have been tried with one local model only

Findings were verified with a fake OpenAI-compatible server. Docker Model Runner with gemma4 accepts the strict JSON schema, but other real models and providers are unchecked.

### 9. Host findings from the daily check are dropped

A finding about the host has no container log lines to cite as evidence, so the daily check drops it.

### 10. Docker Desktop's storage figure can fire the filling-disk rule

On Docker Desktop the storage figure can read high (see known issue 1), which can raise a filling-storage finding that is not real.

## Deliberate limits (decided, not bugs)

- **Sign-in pause is server-wide.** Anyone who can reach Vegapunk can pause sign-in for 15 minutes with five wrong tries. Follows the design; acceptable behind a firewall.
- **A recreated container starts a fresh history** (same name, new Docker id). Plan decision D5.
- **Storage is one filesystem:** the disk Docker keeps its data on, or `VEGAPUNK_DISK_PATH`. No per-filesystem list yet. Plan decision D2.
- **Container CPU is a share of the whole host**, so it reads about a tenth of `docker stats` on a 10-core machine. Plan decision D1.
- **Times are UTC** until a time zone setting exists.
- **Dark theme only.**
- **A stopped or failed answer leaves its question in History without an answer.** The next message sends two questions in a row, which providers accept.
- **No per-message times in the chat.** logpose messages carry no timestamps; History shows when each conversation last changed.
- **Redaction of secrets is best effort.** It catches common shapes of secret, not every possible one.
- **The API key's encryption key sits beside the database.** It protects a copied database, not someone who can read the whole data folder.
- **One answer at a time**, server-wide, with at most 8 model turns and 12 lookups each.
- **An answer keeps running after the browser leaves**, until it ends, hits its limits (180 s, 8 turns, 12 lookups) or someone presses Stop, so a model you are billed for keeps working meanwhile. A sync log read already under way when Stop is pressed finishes in the background.
- **A reattached answer replays merged text.** A reader who joins late gets the text so far as one event; past 5,000 events, step events are no longer kept for late readers (live readers still get them).
- **Long conversations rely on logpose's own context handling,** which summarises when a provider rejects an over-long request.
