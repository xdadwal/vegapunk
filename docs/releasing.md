# Releasing

Nothing is published to a registry. A release is a version tag and GitHub release notes; users build the image from the repository with `docker compose up --build`.

## Versioning policy

Vegapunk follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html): `MAJOR.MINOR.PATCH`, one version for the whole project (the same in `backend/pyproject.toml` and `frontend/package.json`).

**While the version is 0.y.z**, Vegapunk is pre-release and a minor version (0.1 to 0.2) may include breaking changes. They are always listed under "Changed" or "Removed" in [CHANGELOG.md](../CHANGELOG.md), so read it before upgrading. A patch version (0.1.0 to 0.1.1) has fixes only.

**What counts as a stable interface**, so that breaking it is a breaking change:

- the environment variables documented in the [install guide](install.md#configuration);
- the documented options of `compose.yaml` and `compose.socket-proxy.yaml` (the port, `DOCKER_GID`, the data volume, the socket proxy setup);
- the data directory: a newer version opens a data directory written by an older one, and updates it on start with forward-only migrations.

**What is not a stable interface**, and can change in any release:

- the HTTP API under `/api`, which exists for Vegapunk's own web interface;
- the database schema itself, beyond the promise that migrations carry existing data forward;
- the agent's prompt, its tools and what they return.

**Deprecations.** Where practical, something that will be removed is announced as deprecated in the changelog at least one minor release before it is removed.

**Supported versions.** Only the latest release is supported and gets security fixes; see [SECURITY.md](../SECURITY.md#supported-versions).

**Upgrades are forward-only.** An older version refuses a database written by a newer one, so going back means restoring a backup taken before the upgrade ([Upgrading](install.md#upgrading)).

## Checklist

1. Before the first release, enable private vulnerability reporting (repository Settings → Code security) and check it with `gh api repos/xdadwal/vegapunk/private-vulnerability-reporting`, which must report `{"enabled":true}`. `SECURITY.md` tells people to report vulnerabilities that way, and the file is public as soon as a branch containing it is pushed, so do this before pushing a branch that contains `SECURITY.md` (the repository owner does this).
2. Merge the work into `main` (the repository owner does this). For 0.1.0 that is the whole milestone 1 to 5 stack of pull requests.
3. Check that CI is green on `main`.
4. Set the same version in `backend/pyproject.toml` and `frontend/package.json` (from `frontend/`: `npm version X.Y.Z --no-git-tag-version`, which also updates `frontend/package-lock.json`; then run `uv lock` in `backend/`). Commit `backend/uv.lock` with the other files: it records Vegapunk's own version, and `uv sync --locked` (used by the Docker build) fails when it is out of date.
5. In `CHANGELOG.md`, move the entries under `## [Unreleased]` to a new heading for the version and date (`## [X.Y.Z] - YYYY-MM-DD`), and leave `## [Unreleased]` empty above it.
6. Commit (or merge through a pull request if `main` is protected), then tag: `git tag -a vX.Y.Z -m "Vegapunk X.Y.Z"`.
7. Push the commit and the tag: `git push origin main vX.Y.Z` (or, if `main` is protected, merge the commit through a pull request and push only the tag).
8. Create the GitHub release for the tag and paste in that version's section of `CHANGELOG.md`.

## Dependencies updated by hand

Dependabot does not bump these, so check them when preparing a release: `logpose`, a git dependency pinned to a tag in `backend/pyproject.toml` (change the tag, then run `uv lock`), and the digest-pinned proxy image in `compose.socket-proxy.yaml` (see [the image](socket-proxy.md#the-image) in the socket proxy guide).

## Publishing later

To publish an image, add a workflow that builds the image on a version tag and pushes it to a registry, and point `compose.yaml` at it with an `image:` line.
