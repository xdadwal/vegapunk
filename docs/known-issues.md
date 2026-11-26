# Known issues

Things we know are wrong, unverified, or deliberately limited. Each has a status so nothing is lost between milestones. Fix or close an item by editing this file in the same change.

## Open

### 1. Docker Desktop reports the Docker VM, not the machine

**Found:** 2026-11-26, running milestone 2 on macOS with Docker Desktop.

On Docker Desktop (Mac or Windows) containers run in a small Linux VM, so Vegapunk reports that VM:

| | Vegapunk showed | The Mac actually has |
|---|---|---|
| Host name | `docker-desktop` (kernel `linuxkit`) | `MacQueen` |
| Memory | 7.8 GiB (Docker Desktop's limit) | 16 GiB |
| Storage | 1007 GiB used 17 GiB (the VM's sparse disk image) | 460 GiB, 274 GiB used |
| Uptime | since the VM started | since the Mac booted |

CPU cores match only because Docker Desktop gives the VM every core. The **Storage figure is misleading** there: it is bigger than the real disk. Container data (names, states, CPU, memory per container) is real and matches `docker stats`.

On a Linux host the numbers are the machine's. That is the supported target, but it is **not yet verified on Linux** (see 2).

**README is inaccurate:** it says CPU and memory "already report the whole host". True on Linux, not on Docker Desktop.

Candidate fixes (neither done, awaiting a decision):
1. README note that Docker Desktop reports the VM and that Storage is not meaningful there.
2. A small note on the Overview when the host's OS is `Docker Desktop`.

### 2. Not verified on a real Linux host

Everything was run against Docker Desktop, where the Docker socket is open to every user. On Linux the socket belongs to the `docker` group, which is why compose uses `group_add: ${DOCKER_GID}`. That path, and the claim that a container's own `/proc` is host-wide, are untested on Linux.

### 3. The pages have not been looked at in a browser by the builder

Layout, charts at real widths and phone width are covered by tests only. The user has seen the Overview, but the drawer, charts on phones and keyboard use of the account menu and chart have not been checked by eye.

### 4. "Old values are hidden after 45 s" not observed live

Covered by unit tests. A live run did not wait long enough to see a stale container's values disappear.

### 5. Backend test warning

`httpx` is deprecated for Starlette's test client, which prints a warning in every backend test run. Switching to `httpx2` was attempted and blocked by the permission system.

### 6. The agent has not been tried against a real AI provider

Milestone 3 was verified against a scripted fake OpenAI-compatible server over real HTTP, which exercises logpose's real code path. Real models differ: how well they use the tools, how long answers take, and how a given provider words its errors are unchecked until someone connects one.

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
- **Long conversations rely on logpose's own context handling,** which summarises when a provider rejects an over-long request.
