# Vegapunk

An open-source tool for managing a homelab host and its services.

> **Status:** Early development — things will change quickly.

## Overview

Vegapunk is a single server that runs on your homelab host. It periodically captures data about the host and the services running on it, and gives you a web interface to view and manage them.

## Run with Docker

```sh
DOCKER_GID=$(stat -c %g /var/run/docker.sock) docker compose up -d --build
```

Open http://localhost:8000 and create the admin account. Data lives in the `vegapunk-data` volume. Vegapunk starts capturing straight away; the first CPU readings appear after the second capture.

- **Docker access.** Vegapunk reads container state and usage through the Docker socket. The socket gives this container control of Docker, whatever the mount flags say, so treat Vegapunk as having that power. Vegapunk only reads from it, in one small module (`backend/vegapunk/host/docker.py`) that has no way to start, stop or change anything.
- **`DOCKER_GID`.** The container runs as a non-root user and reaches the socket through the group that owns it. If the page says "Vegapunk can't read the Docker socket", set `DOCKER_GID` as above.
- **What is measured.** CPU, memory and uptime come from the container's own `/proc`, which already reports the whole host. Storage is the disk Docker keeps its data on; to measure another disk, mount a folder on it read-only and set `VEGAPUNK_DISK_PATH` to that path.
- **How often.** Every 15 seconds; set `VEGAPUNK_CAPTURE_INTERVAL` (seconds) to change it. Detail is kept for a day and 5-minute averages for 30 days.
- **Forgot your password?** Run `docker exec -it vegapunk python -m vegapunk reset-password` on the host.
- **Behind an HTTPS reverse proxy?** Set `VEGAPUNK_SECURE_COOKIES=1` so the sign-in cookie is only sent over HTTPS.
- **Using a folder instead of the volume?** The container runs as user ID 10001, so that user must be able to write to it.

## Development

You need [uv](https://docs.astral.sh/uv/) and Node.js 24.

```sh
# Backend (http://localhost:8000)
cd backend
uv sync
uv run uvicorn vegapunk.main:app --reload

# Frontend (in a second terminal)
cd frontend
npm install
npm run dev
```

## Contributing

Contributions, ideas, and bug reports are welcome — feel free to open an issue or pull request.

## License

Released under the [MIT License](LICENSE).
