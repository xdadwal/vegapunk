# Optional: a read-only Docker socket proxy

By default Vegapunk reads Docker through the Docker socket, mounted into its container. This page explains how to put a proxy between the two so that Docker itself refuses anything but reads.

## Why

Whoever can use the Docker socket controls Docker. That means they can start any container, mount any folder of the host into it, and so act as root on the host. Mounting the socket read-only (`:ro`) does not change that: the flag stops the socket file from being replaced, not requests from being sent through it.

Vegapunk only reads. All its Docker calls are in one small module that has no way to start, stop or change anything. But that is a promise made by Vegapunk's code. If someone found a way to make Vegapunk send other requests, the socket would carry them.

The proxy makes the promise hold outside Vegapunk. Vegapunk no longer gets the socket. It gets the address of the proxy, which passes on only the reads Vegapunk needs and answers everything else with `403 Forbidden`.

## What changes and what does not

What changes:

- A second container, `socket-proxy`, runs [`tecnativa/docker-socket-proxy`](https://github.com/Tecnativa/docker-socket-proxy) (HAProxy with a fixed set of rules). It is the only container with the Docker socket, mounted read-only.
- Vegapunk's container no longer has the socket and no longer needs `DOCKER_GID`. It reaches Docker at `tcp://socket-proxy:2375` (set with `VEGAPUNK_DOCKER_URL`).
- Only `GET` and `HEAD` requests are passed on, and only to these parts of the Docker API:

  | Setting | Path | What Vegapunk reads there |
  |---|---|---|
  | `PING=1` | `/_ping` | Whether Docker answers |
  | `VERSION=1` | `/version` | The Docker version (the Docker SDK also asks for it when it connects) |
  | `INFO=1` | `/info` | Host facts |
  | `CONTAINERS=1` | `/containers/...` | The container list, each container's details, one-shot stats and logs |

  Every other part of the API (images, volumes, networks, events, exec, build, swarm and the rest) is refused, and every `POST`, `PUT` and `DELETE` is refused, even under `/containers`. All settings are written out in [`compose.socket-proxy.yaml`](../compose.socket-proxy.yaml), so a default in the image cannot turn one on.
- The proxy has no published port. Only containers in the same Compose project can reach it.
- The proxy runs with a read-only root filesystem, all Linux capabilities dropped and `no-new-privileges`.

What does not change:

- Vegapunk shows the same data, and the agent can read container logs as before.
- Your data. Vegapunk keeps the same data volume, so you can switch the proxy on and off without losing anything.
- The proxy still has full access to the socket, and `CONTAINERS=1` allows every read under `/containers`. That includes **reading files from inside any container** (`GET /containers/<id>/archive`, and `GET /containers/<id>/export` for a container's whole filesystem) and every container's details, which include its environment variables. Vegapunk never reads container files and never shows or passes on environment variables, but the proxy does not stop these reads. The proxy turns "can control Docker" into "can read everything Docker knows about your containers", not into "can read only what Vegapunk shows".

## Turn it on

You need Docker Compose 2.24.4 or newer: the override file uses Compose's `!override` and `!reset` tags, which older versions do not understand. Check with `docker compose version`. (Checked with Compose v5.3.0.)

From the folder you cloned Vegapunk into:

```sh
docker compose -f compose.yaml -f compose.socket-proxy.yaml up -d --build
```

Compose starts the proxy, waits until it answers, then recreates Vegapunk without the socket. `DOCKER_GID` is no longer needed.

From now on, give both files to every `docker compose` command for this install (`up`, `logs`, `down`, and so on). A plain `docker compose up -d` goes back to the direct socket. To avoid typing them, put this line in a file named `.env` in the same folder, and Compose will use both files on its own:

```sh
COMPOSE_FILE=compose.yaml:compose.socket-proxy.yaml
```

(On Windows the separator is `;`, not `:`.)

## Turn it off

```sh
docker compose -f compose.yaml up -d --remove-orphans
```

Vegapunk is recreated with the socket again and `--remove-orphans` removes the proxy container. If you set `COMPOSE_FILE` in `.env`, remove that line first. On Linux, set `DOCKER_GID` again as in the install guide.

## Check that it works

Vegapunk should show your containers as usual. To see that a write is refused, ask the proxy to stop a container from inside Vegapunk's container. The image has no `curl`, so this uses Python:

```sh
docker exec vegapunk python -c "
import urllib.request, urllib.error
try:
    urllib.request.urlopen(urllib.request.Request(
        'http://socket-proxy:2375/containers/no-such-container/stop', method='POST'), timeout=5)
except urllib.error.HTTPError as e:
    print(e.code)
"
```

- `403` means the proxy refused the request: it is working.
- `404` means the request reached Docker, which then found no such container: the proxy is not refusing writes. Check that you started Vegapunk with both files.
- `urllib.error.URLError: <urlopen error [Errno -2] Name or service not known>` means there is no proxy for Vegapunk to reach: it is running without the override file.

The command names a container that does not exist, so it is safe to run even if the proxy is not working.

To check that Vegapunk no longer has the socket:

```sh
docker exec vegapunk ls /var/run/docker.sock
```

This should say `No such file or directory`.

## The image

The proxy is a third-party image, maintained by Tecnativa, not by Vegapunk. It is optional and only appears in the override file.

It is pinned to the `3.4` tag (HAProxy 3.4) and to that tag's digest, so you run exactly the image this file was tested with: `tecnativa/docker-socket-proxy:3.4@sha256:027f9fa5ee70be5836eeaa10ce889d7a83f34a8db16a7f412818b92997e0beab` (built 2026-11-17, HAProxy 3.4.7). Upstream rebuilds its supported tags (`3.4`, `lts`, `latest`) every week and has stopped publishing version tags such as `v0.5.0`, so a tag alone would change under you.

The cost of pinning is that you do not get the weekly rebuilds (which carry Alpine and HAProxy fixes) until the pin is updated. To move to the current `3.4` build yourself, look up its digest:

```sh
docker buildx imagetools inspect tecnativa/docker-socket-proxy:3.4 --format '{{.Manifest.Digest}}'
```

and put it after `@` in the `image:` line of `compose.socket-proxy.yaml`, then run the "turn it on" command again. Read the [upstream releases](https://github.com/Tecnativa/docker-socket-proxy/releases) before you do, and run the check above afterwards.

## Docker Desktop and Linux

- **Docker Desktop (macOS):** verified. Everything on this page was run on Docker Desktop on macOS with Docker Engine 29.6.1: Vegapunk captured host and container data, stats and logs through the proxy, and writes (`stop`, `restart`, `kill`, `pause`, `DELETE`, `create`, `exec`) and other parts of the API (`/images`, `/volumes`, `/networks`, `/events`, `/system`) were refused with `403`.
- **Linux:** not verified; it is the supported target, but this page has not been run on a Linux host. What should differ:
  - The proxy runs as root inside its container, and the socket on Linux is usually owned by `root` with group `docker` and mode `660`. Root owns it, so the proxy can open it even with all capabilities dropped.
  - On hosts with SELinux enforcing (Fedora, RHEL and similar) or a strict AppArmor profile, the proxy may be denied the socket. Upstream's instructions run the proxy with `--privileged` for that reason; this file does not, because it gives the proxy far more than it needs. If the proxy logs permission errors on such a host, see your distribution's documentation for letting a container use the Docker socket.
  - With rootless Docker the socket is not at `/var/run/docker.sock`. Change the left-hand side of the proxy's `volumes` line to your socket's path (for example `$XDG_RUNTIME_DIR/docker.sock`).

## Troubleshooting

**The page says Vegapunk can't reach Docker (`Can't reach Docker at tcp://socket-proxy:2375.`).** The proxy is not running or not answering. Check it:

```sh
docker compose -f compose.yaml -f compose.socket-proxy.yaml ps
docker compose -f compose.yaml -f compose.socket-proxy.yaml logs socket-proxy
```

Once the proxy is back, Vegapunk picks it up at the next capture; it does not need a restart.

**The page shows `403 Client Error ... Forbidden`.** The proxy refused a read Vegapunk needs. The error names the path. This should not happen with the settings in `compose.socket-proxy.yaml`; if you changed them, compare with the table above. A newer Vegapunk that reads something new would update this file too.

**You want to see each request the proxy receives.** The proxy logs only its own notices by default, because Vegapunk asks Docker something every few seconds and a line per request adds up. Set `LOG_LEVEL: info` in the proxy's `environment` in `compose.socket-proxy.yaml`, run the "turn it on" command, and read the proxy's logs as above. Refused requests show as `403`. Set it back afterwards.

**`docker compose` says `!override` or `!reset` is not valid.** Your Compose is older than 2.24.4. Update Docker or the Compose plugin.
