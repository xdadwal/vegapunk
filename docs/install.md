# Installing and running Vegapunk

This guide covers installing Vegapunk, configuring it, backing it up, upgrading, removing it, and fixing common problems. The [README](../README.md) has the short version.

Vegapunk is built from this repository on your machine. No image is published to a registry.

- [What you need](#what-you-need)
- [Install](#install)
- [First sign-in and the AI provider](#first-sign-in-and-the-ai-provider)
- [How Vegapunk reads your host](#how-vegapunk-reads-your-host)
- [Configuration](#configuration)
- [Data and backups](#data-and-backups)
- [Upgrading](#upgrading)
- [Uninstalling](#uninstalling)
- [Troubleshooting](#troubleshooting)
- [What has been verified](#what-has-been-verified)

## What you need

- The machine whose containers you want to watch. Vegapunk runs on it, as a container, and watches only that machine.
- Docker with the Compose plugin (`docker compose`, not the older `docker-compose`). The optional [socket proxy](socket-proxy.md) needs Compose 2.24.4 or newer.
- Git, to get the code.

Check what you have:

```sh
docker compose version
```

## Install

Get the code and go into its folder:

```sh
git clone https://github.com/xdadwal/vegapunk.git
cd vegapunk
```

Then build and start it. The command differs between Linux and Docker Desktop.

**On Linux,** the container reaches the Docker socket through the group that owns it (usually `docker`), and that group's id differs from machine to machine. Pass it in with `DOCKER_GID`:

```sh
DOCKER_GID=$(stat -c %g /var/run/docker.sock) docker compose up -d --build
```

`stat -c` is the GNU (Linux) form; it does not work on macOS. Not verified: this command has not been run on a Linux host yet (see [What has been verified](#what-has-been-verified)).

**On Docker Desktop (macOS or Windows),** leave `DOCKER_GID` out:

```sh
docker compose up -d --build
```

`DOCKER_GID` defaults to `0`, the root group. That works where the socket is open to the root group or to everyone, as it is inside Docker Desktop's VM (`root:root`, mode 660). It does not work on a normal Linux host, where the socket is `root:docker`.

The first build takes a few minutes. When it is done, open http://localhost:8000. If port 8000 is already taken, pick another one with `VEGAPUNK_PORT`:

```sh
VEGAPUNK_PORT=8080 docker compose up -d --build
```

To avoid typing `DOCKER_GID` or `VEGAPUNK_PORT` every time, put them in a file called `.env` next to `compose.yaml`; Compose reads it on its own, and git ignores it:

```sh
VEGAPUNK_PORT=8080
DOCKER_GID=998
```

The container is called `vegapunk`. Check that it is running and healthy (it shows `health: starting` for its first few seconds) and which version it is:

```sh
docker compose ps
docker exec vegapunk python -m vegapunk version
```

## First sign-in and the AI provider

The first time you open Vegapunk it asks you to create the admin account: a username and a password of at least 10 characters. There is only one account.

Next it asks for an AI provider for the agent. You can skip this and add one later in Settings; monitoring works without it. Any server that speaks the OpenAI API works: enter its base URL, an API key if it needs one, and a model, and use "Test connection" to list the models it offers. The [README](../README.md#the-agent) has the details, including what is sent to the provider.

Some base URLs:

- OpenAI: `https://api.openai.com/v1`.
- Ollama on the same machine: `http://host.docker.internal:11434/v1`, no key. Ollama must listen on an address the container can reach (start it with `OLLAMA_HOST=0.0.0.0`). On Docker Desktop the name `host.docker.internal` is built in; on Linux it comes from the `extra_hosts` line in `compose.yaml`.
- Docker Model Runner (Docker Desktop's local models): `http://model-runner.docker.internal/engines/v1`, no key. "Test connection" against this address listed `docker.io/ai/gemma4:latest` on Docker Desktop for macOS.

## How Vegapunk reads your host

- **Docker access.** Vegapunk reads container state and usage through the Docker socket. The socket gives this container control of Docker, whatever the mount flags say, so treat Vegapunk as having that power. Vegapunk only reads from it, in one small module (`backend/vegapunk/host/docker.py`) that has no way to start, stop or change anything. To take that power away from Vegapunk's container, put the optional [read-only socket proxy](socket-proxy.md) in front of Docker.
- **`DOCKER_GID`.** The container runs as a non-root user and reaches the socket through the group that owns it. If the page says "Vegapunk can't read the Docker socket", set `DOCKER_GID` as shown in [Install](#install).
- **What is measured.** CPU, memory and uptime come from the container's own `/proc`, which on Linux already reports the whole host. Storage is the disk Docker keeps its data on; to measure another disk, mount a folder on it read-only and set `VEGAPUNK_DISK_PATH` to that path. On Docker Desktop these numbers describe Docker's VM, not your computer (see [Troubleshooting](#docker-desktop-shows-the-wrong-memory-storage-or-host-name)).
- **How often.** Every 15 seconds; set `VEGAPUNK_CAPTURE_INTERVAL` (seconds) to change it. Detail is kept for a day and 5-minute averages for 30 days.
- **Logs are not stored.** The agent reads a container's logs live from Docker when it needs them; only the lines it cites as evidence in a finding are kept.

## Configuration

Two settings are read by Compose when it starts the container. Set them in the shell or in `.env`:

| Variable | Default | What it does |
|---|---|---|
| `VEGAPUNK_PORT` | `8000` | The port on the host that Vegapunk is published on. |
| `DOCKER_GID` | `0` | The group id that owns the Docker socket. Needed on Linux; see [Install](#install). |

The rest are read by Vegapunk itself, once at start:

| Variable | Default | What it does |
|---|---|---|
| `VEGAPUNK_SECURE_COOKIES` | off | Set to `1` when Vegapunk is behind an HTTPS reverse proxy, so the sign-in cookie is only sent over HTTPS. |
| `VEGAPUNK_CAPTURE_INTERVAL` | `15` | Seconds between captures. A whole number, 1 or more; anything else stops Vegapunk from starting. |
| `VEGAPUNK_DISK_PATH` | `/` | The path whose disk is shown as Storage. `/` inside the container is the disk Docker keeps its data on. |
| `VEGAPUNK_DOCKER_URL` | `unix:///var/run/docker.sock` | Where Docker is. The [socket proxy](socket-proxy.md) sets it to `tcp://socket-proxy:2375`. |
| `VEGAPUNK_DATA_DIR` | `/data` in the image | Where the database and key file live. Leave it alone: the volume is mounted there. |
| `VEGAPUNK_STATIC_DIR` | `/app/static` in the image | The built web interface. Set by the image; leave it alone. |

The AI provider is not set here. It is set in the app, under Settings.

To set one of Vegapunk's own variables, create a file called `compose.override.yaml` next to `compose.yaml`. Compose merges it in on its own whenever you run `docker compose` without `-f`:

```yaml
services:
  vegapunk:
    environment:
      VEGAPUNK_SECURE_COOKIES: "1"
      VEGAPUNK_CAPTURE_INTERVAL: "30"
```

Then run `docker compose up -d` again. If you use the socket proxy, Compose no longer picks the file up on its own; name it yourself: `docker compose -f compose.yaml -f compose.socket-proxy.yaml -f compose.override.yaml up -d`.

**Using a folder instead of the volume?** The container runs as user ID 10001, so that user must be able to write to it. For example, replace `vegapunk-data:/data` in `compose.yaml` with `./data:/data` and, on Linux, run `sudo chown 10001 data` once. Not verified: a folder mount has not been run.

## Data and backups

Everything Vegapunk keeps is in one Docker volume, mounted at `/data` in the container:

- `vegapunk.db`: the admin account, settings, captured history and findings.
- `conversations.db`: the agent conversations (created the first time you use the chat).
- `secret.key`: the key that encrypts the saved AI provider API key.

The volume is called `<folder>_vegapunk-data`, where `<folder>` is the name of the folder you cloned into (`vegapunk_vegapunk-data` if you kept the default). To be sure, ask Docker:

```sh
docker inspect vegapunk --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}'
```

### Back up

Stop Vegapunk first. The databases are written all the time and a copy taken while they are open can miss recent changes. Stopping takes a few seconds and monitoring resumes when you start it again.

```sh
VOLUME=$(docker inspect vegapunk --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}')
docker compose stop
docker run --rm -v "$VOLUME":/data:ro python:3.12-slim tar -C /data -cpf - . > vegapunk-backup.tar
docker compose start
```

`python:3.12-slim` is the image Vegapunk is built on, so it is already on your machine; any image with GNU tar works. The archive keeps file ownership. Check what is in it:

```sh
tar -tvf vegapunk-backup.tar
```

Keep the backup somewhere safe and private. It holds `secret.key` and the encrypted API key side by side, so anyone with the file can read your API key.

### Restore

Restore into an empty volume. On the same machine, remove the container and the old volume first (this deletes the current data, so be sure the backup is good):

```sh
VOLUME=$(docker inspect vegapunk --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}')
docker compose down
docker volume rm "$VOLUME"
```

On a new machine, clone the repository first and use `<folder>_vegapunk-data` as the volume name. Then create the volume, unpack the backup into it as root, and give the files back to user 10001, which Vegapunk runs as and which must be able to write them:

```sh
docker volume create "$VOLUME"
docker run --rm -i -v "$VOLUME":/data python:3.12-slim \
  sh -c 'tar -C /data --same-owner -xpf - && chown -R 10001 /data' < vegapunk-backup.tar
docker compose up -d --build
```

Compose may warn that the volume "already exists but was not created by Docker Compose". That is expected, and it uses the volume.

Sign in with the same username and password as before.

**Without `secret.key`.** If the backup has no `secret.key`, Vegapunk still starts, everything else is there, and it makes a new key. The saved API key can no longer be read: Settings says "The saved API key can't be read. Enter it again." Enter it again and the agent works as before.

## Upgrading

Upgrades are done by pulling the new code and rebuilding. The database is updated on start.

1. Back up first ([Back up](#back-up)). Going back to an older version means restoring that backup: an older Vegapunk refuses a database written by a newer one.
2. Get the new code and rebuild:

   ```sh
   git pull
   docker compose up -d --build
   ```

   On Linux, pass `DOCKER_GID` as you did when installing (or keep it in `.env`).
3. Check the version and that it is healthy:

   ```sh
   docker exec vegapunk python -m vegapunk version
   docker compose ps
   ```

The admin account, settings, history and findings carry over. The changes between versions are in the [changelog](../CHANGELOG.md).

## Uninstalling

Stop and remove the container, keeping the data volume (you can start again later with `docker compose up -d`):

```sh
docker compose down
```

To remove the data too, add `-v`. **This deletes the admin account, settings, history, findings, conversations and the saved API key, and cannot be undone.** Back up first if you might want them:

```sh
docker compose down -v --rmi local
```

`--rmi local` also removes the image that was built. Then delete the cloned folder.

## Troubleshooting

Start with the logs; most problems say what is wrong there:

```sh
docker compose logs vegapunk
```

### "Vegapunk can't read the Docker socket"

The container cannot use the Docker socket, so Containers stays empty and the page shows the message. On Linux, start it with the socket's group id as shown in [Install](#install):

```sh
DOCKER_GID=$(stat -c %g /var/run/docker.sock) docker compose up -d
```

With the socket proxy, Vegapunk does not use the socket at all; see [its troubleshooting](socket-proxy.md#troubleshooting).

### Port 8000 is already in use

`docker compose up` fails with `Bind for 0.0.0.0:8000 failed: port is already allocated`. Use another port:

```sh
VEGAPUNK_PORT=8080 docker compose up -d
```

Then open http://localhost:8080.

### It keeps restarting after going back to an older version

If you ran a newer Vegapunk and then started an older one on the same data, the older one refuses to start, and Docker restarts it over and over (`docker compose ps` shows it restarting or up for less than a second). The logs say:

```text
The database is at version 6, but this Vegapunk only knows version 3. It was written by a newer Vegapunk; upgrade instead of downgrading.
```

Go back to the newer version (`git checkout` it and `docker compose up -d --build`), or restore the backup you took before upgrading ([Restore](#restore)).

### Forgot your password

Run this on the host. It asks for the new password twice and signs everyone out:

```sh
docker exec -it vegapunk python -m vegapunk reset-password
```

### "Your AI provider rejected the API key"

The provider turned the key down (it was revoked, expired or mistyped). The Agent page and Settings say so and the chat pauses until you save a working key in Settings. Monitoring is not affected.

### Nothing is shown yet

Vegapunk captures every 15 seconds from the moment it starts. Right after the first start the pages are empty for a few seconds, and CPU appears only after the second capture, because CPU use is worked out from the change between two readings.

### Docker Desktop shows the wrong memory, storage or host name

On Docker Desktop (Mac or Windows), containers run in a small Linux VM, and Vegapunk reports that VM: its host name (`docker-desktop`), its memory limit, its uptime, and the VM's disk image as Storage, which can be larger than your real disk. Container data is real. This is [known issue 1](known-issues.md#1-docker-desktop-reports-the-docker-vm-not-the-machine). On a Linux host the numbers are the machine's.

### Behind an HTTPS reverse proxy

Set `VEGAPUNK_SECURE_COOKIES=1` (see [Configuration](#configuration)) so the sign-in cookie is only sent over HTTPS. Vegapunk does not serve HTTPS itself.

### "Can't use the data directory"

Vegapunk cannot write to `/data`. This happens with a folder mount (or a restored volume) owned by another user. Make it writable by user 10001, for a folder `sudo chown -R 10001 data` on Linux, or for a volume the `chown` step in [Restore](#restore).

## What has been verified

- **Docker Desktop for macOS**: the commands in this guide were run there, under throwaway project names so an existing install was not touched: install, the health check, first sign-in, settings through `compose.override.yaml` and `.env`, back up and restore (including without `secret.key`), upgrading from earlier builds, the downgrade refusal, password reset, a port already in use, a wrong `DOCKER_GID`, and the socket proxy. Cloning from GitHub and Windows were not tried.
- **Linux** is the supported target, but it has not been run by the author yet. The `DOCKER_GID` line and the folder mount are therefore not verified.
- **AI providers**: no hosted provider has been tried, and neither has Ollama. The one local model tried is Gemma (`docker.io/ai/gemma4:latest`) through Docker Model Runner.
- **The socket proxy** was checked on Docker Desktop for macOS; see [socket-proxy.md](socket-proxy.md).

Known problems are listed in [known-issues.md](known-issues.md).
