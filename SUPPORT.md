# Getting help

Vegapunk is a pre-release project with one maintainer, answering in spare time. Here is where to go, so your question reaches the right place.

## Read first

- [README](README.md): what Vegapunk is and the quick start.
- [Install guide](docs/install.md): configuration, backups, upgrading and [troubleshooting](docs/install.md#troubleshooting).
- [Known issues](docs/known-issues.md): problems that are already known, and deliberate limits.
- [Socket proxy](docs/socket-proxy.md), if you use it.

## Where to ask

| You have | Go to |
|---|---|
| A question, or need help getting it running | [Discussions](https://github.com/xdadwal/vegapunk/discussions) |
| An idea or a feature request | [Discussions](https://github.com/xdadwal/vegapunk/discussions), or the feature request form under [Issues](https://github.com/xdadwal/vegapunk/issues/new/choose) |
| A bug: something that doesn't work as the docs say | The bug report form under [Issues](https://github.com/xdadwal/vegapunk/issues/new/choose) |
| A security vulnerability | **Not a public issue.** Follow [SECURITY.md](SECURITY.md) and report it privately |

## What to put in a bug report

The bug form asks for these; having them ready saves a round of questions.

- **The version.** Run:

  ```sh
  docker exec vegapunk python -m vegapunk version
  ```

- **Your platform**: Docker Desktop on macOS or Windows, Docker on Linux (which distribution), or something else, and the output of `docker compose version`.
- **Whether you use the [socket proxy](docs/socket-proxy.md).**
- **What you did, what you expected, and what happened instead.** Steps to reproduce help most.
- **The logs**, from the folder you installed Vegapunk in:

  ```sh
  docker compose logs vegapunk
  ```

**Remove secrets before you paste anything.** Logs, settings and screenshots can contain API keys, passwords, tokens, host names and addresses. Replace them with something like `[redacted]`. Never post your `secret.key`, a backup of the data volume, or your AI provider's API key.

## What to expect

Replies are best effort, with no guaranteed response time. Issues that include the details above are much quicker to act on. If you can fix the problem yourself, see [CONTRIBUTING.md](CONTRIBUTING.md).
