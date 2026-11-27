# Changelog

All notable changes to Vegapunk are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-27

First version. Vegapunk watches one Docker host and reports problems; it changes nothing on the host.

### Added

- Setup of the admin account and the AI provider, sign-in, and a Profile page. A `reset-password` command sets a new password from the host, and `python -m vegapunk version` prints the version.
- Capture of container status, restart counts and uptime, CPU and memory per container and for the host, and storage used, with history kept for trends.
- Overview, Containers and Container detail screens.
- Agent chat with a History page. The AI provider can be any OpenAI-compatible endpoint, set in Settings with your own API key.
- Findings: a daily check and a background watch, with rules for restart loops, out-of-memory kills, sustained high CPU or memory, and filling storage. Each finding carries a diagnosis and verified evidence.
- A Findings page and a Finding page, and chat linked to a finding.
- Answers that keep running when you leave the page, with a Stop button.
- A switch for the background watch, and Settings → Checks.
