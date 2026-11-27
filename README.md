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

## The agent

Vegapunk has an agent you can ask about your host: "Is anything wrong right now?", "Why is a container restarting?". It answers by reading what Vegapunk has captured, plus a container's live logs. **It only reads. It can't start, stop or change anything.**

It runs on your own AI provider. After you create the admin account, Vegapunk asks for one; you can skip that and add it later in Settings.

- **Which provider.** Any server that speaks the OpenAI API: OpenAI itself, OpenRouter, or a local server such as Ollama or vLLM. Enter its base URL, an API key if it needs one, and a model. "Test connection" lists the models the server offers.
  - OpenAI: base URL `https://api.openai.com/v1`.
  - Ollama on the same machine: base URL `http://host.docker.internal:11434/v1`, no key needed. (Ollama must listen on an address the container can reach; by default it listens only on `127.0.0.1`, so start it with `OLLAMA_HOST=0.0.0.0`.)
- **What is sent to the provider.** Your questions, what the agent reads to answer them (container names, images, state, CPU and memory), and log lines with secrets such as passwords, tokens, keys, private keys and credentials in URLs replaced by `[redacted]`. Redaction is best effort: it catches common shapes of secret, not every possible one, so don't point the agent at a provider you don't trust with your logs. Container environment variables are never read. Your instructions in Settings go to the provider too.
- **Where the API key is kept.** Encrypted in Vegapunk's database, with the encryption key in `secret.key` beside it in the data volume. That keeps the key out of a copied database or backup. It does not protect against someone who can read the whole data volume, because the key and the data sit side by side. A saved key is only ever sent to the base URL it was saved for.
- **Limits.** One answer at a time; at most 8 model turns and 12 lookups per answer; messages up to 4,000 characters.
- **If the key stops working,** Vegapunk says so on the Agent page and in Settings and pauses the chat until you save a working one. Monitoring is not affected.

## Findings

The agent looks for problems on its own, in two ways. A daily check (at the hour you choose in Settings) asks your AI provider to review every container and the host. A background watch uses simple rules (a restart loop, a memory kill, sustained high CPU or memory, filling storage) and calls the provider only when one fires, at most 20 times a day. Both send container logs and metrics to your AI provider. You can turn the daily check off in Settings. The background watch has no switch of its own: it runs whenever an AI provider is saved, so remove the provider to stop it.

Each finding has a diagnosis, the evidence it cites and a suggested fix in words. You can mark findings as reviewed; in this version Vegapunk does not change anything on your host. Secrets are redacted from logs before they are sent, but redaction is best effort and does not catch every possible secret.

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
