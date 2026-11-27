# Releasing

Nothing is published to a registry. A release is a version tag and GitHub release notes; users build the image from the repository with `docker compose up --build`.

## Checklist

1. Merge the work into `main` (the repository owner does this). For 0.1.0 that is the whole milestone 1 to 5 stack of pull requests.
2. Check that CI is green on `main`.
3. Set the same version in `backend/pyproject.toml` and `frontend/package.json` (from `frontend/`: `npm version X.Y.Z --no-git-tag-version`; then run `uv lock` in `backend/`).
4. Move the new entries in `CHANGELOG.md` under a heading for the version and date.
5. Commit, then tag: `git tag -a vX.Y.Z -m "Vegapunk X.Y.Z"`.
6. Push the commit and the tag: `git push origin main vX.Y.Z`.
7. Create the GitHub release for the tag and paste in that version's section of `CHANGELOG.md`.

## Publishing later

To publish an image, add a workflow that builds the image on a version tag and pushes it to a registry, and point `docker-compose.yml` at it with an `image:` line.
