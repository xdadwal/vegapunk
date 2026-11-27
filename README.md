# Vegapunk

An open-source tool for managing a homelab host and its services.

> **Status:** Pre-release, version 0.1.0. It watches and reports; it does not change anything on your host yet. Expect things to change.

## Overview

Vegapunk is a single server that runs on your homelab host. It periodically captures data about the host and the containers running on it, and gives you a web interface to view them.

## Quick start

You need Docker with Compose.

```sh
git clone https://github.com/xdadwal/vegapunk.git
cd vegapunk
docker compose up -d --build
```

On Linux, start it this way instead, so it can read the Docker socket:

```sh
DOCKER_GID=$(stat -c %g /var/run/docker.sock) docker compose up -d --build
```

Then open http://localhost:8000 and create the admin account. If port 8000 is taken, set `VEGAPUNK_PORT` (for example `VEGAPUNK_PORT=8080`).

- [Install guide](docs/install.md): configuration, backups, upgrading, uninstalling and troubleshooting.
- [Read-only Docker socket proxy](docs/socket-proxy.md) (optional).
- [Security](SECURITY.md): what Vegapunk protects and how to report a problem.

## The agent

Vegapunk has an agent you can ask about your host: "Is anything wrong right now?", "Why is a container restarting?". It answers by reading what Vegapunk has captured, plus a container's live logs. **It only reads. It can't start, stop or change anything.**

It runs on your own AI provider. After you create the admin account, Vegapunk asks for one; you can skip that and add it later in Settings.

- **Which provider.** Any server that speaks the OpenAI API: OpenAI itself, OpenRouter, or a local server such as Ollama or vLLM. Enter its base URL, an API key if it needs one, and a model. "Test connection" lists the models the server offers.
  - OpenAI: base URL `https://api.openai.com/v1`.
  - Ollama on the same machine: base URL `http://host.docker.internal:11434/v1`, no key needed. (Ollama must listen on an address the container can reach; by default it listens only on `127.0.0.1`, so start it with `OLLAMA_HOST=0.0.0.0`.)
- **What is sent to the provider.** Your questions, what the agent reads to answer them (container names, images, state, CPU and memory), and log lines with secrets such as passwords, tokens, keys, private keys and credentials in URLs replaced by `[redacted]`. Redaction is best effort: it catches common shapes of secret, not every possible one, so don't point the agent at a provider you don't trust with your logs. Container environment variables are never read. Your instructions in Settings go to the provider too.
- **Where the API key is kept.** Encrypted in Vegapunk's database, with the encryption key in `secret.key` beside it in the data volume. That keeps the key out of a copied database or backup. It does not protect against someone who can read the whole data volume, because the key and the data sit side by side. A saved key is only ever sent to the base URL it was saved for.
- **Limits.** One answer at a time; at most 8 model turns and 12 lookups per answer; messages up to 4,000 characters.
- **Leaving the page does not stop an answer.** It keeps going on the server and shows when you come back; use Stop to cancel it.
- **If the key stops working,** Vegapunk says so on the Agent page and in Settings and pauses the chat until you save a working one. Monitoring is not affected.

## Findings

The agent looks for problems on its own, in two ways. A daily check (at the hour you choose in Settings) asks your AI provider to review every container and the host. A background watch uses simple rules (a restart loop, a memory kill, sustained high CPU or memory, filling storage) and calls the provider only when one fires, at most 20 times a day. Both send container logs and metrics to your AI provider. You can turn the daily check and the background watch off separately in Settings, under Checks.

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
