# Governance

Vegapunk is a young, pre-release project (0.1.0) with one maintainer. This page says how it is run today. It is short because the project is small; it will grow when the project does.

## How the project is run

Vegapunk is **maintainer-led**. The maintainer listed in [MAINTAINERS.md](MAINTAINERS.md) has the final say on what is merged, what is in scope and when releases happen. The maintainer is also the code owner ([.github/CODEOWNERS](.github/CODEOWNERS)), so every pull request needs their approving review.

Decisions are made in the open:

- **Small changes** (fixes, tests, docs, small features within scope) are decided in the pull request by **lazy consensus**: if nobody raises a concern and the maintainer approves, it is merged.
- **Larger changes** (a new dependency, language or framework, a change to the architecture, anything that touches security) are discussed in an [issue](https://github.com/xdadwal/vegapunk/issues) or in [Discussions](https://github.com/xdadwal/vegapunk/discussions) before code is written. The decision and the reason are written down there.
- **Lasting decisions** are recorded in [AGENTS.md](AGENTS.md), which holds the scope, architecture and conventions.

## Scope

The scope of the first version is owned by the maintainer and recorded in [AGENTS.md](AGENTS.md#first-version-scope). [CONTRIBUTING.md](CONTRIBUTING.md#what-the-first-version-is) has a short version.

To propose a change to the scope, start a thread in [Discussions](https://github.com/xdadwal/vegapunk/discussions) (or open a feature request) that says what you want to change, why, and what it means for the planned work. Please wait for an answer before building it. If the change is accepted, the maintainer updates AGENTS.md, and the work can start.

## Releases

The maintainer makes releases, following [docs/releasing.md](docs/releasing.md), which also has the versioning policy. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Code of conduct

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md). The maintainer enforces it, and it says how to report a problem privately, including what to do if the report concerns the maintainer.

## Disagreements

Most disagreements are settled in the issue or pull request where they come up. Explain your reasoning, assume good faith, and stay on the technical question. If that does not settle it, the maintainer decides and says why. A decision can be revisited when there is new information: open a new issue or discussion that links the old one.

## Changing this document

Changes to governance are proposed as a pull request to this file and left open for comment for at least a week before they are merged. When the project gains more maintainers, this page should change with it (for example, how maintainers decide among themselves).
