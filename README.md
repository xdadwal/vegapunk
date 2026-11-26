# Vegapunk

An open-source tool for managing a homelab host and its services.

> **Status:** Early development — things will change quickly.

## Overview

Vegapunk is a single server that runs on your homelab host. It periodically captures data about the host and the services running on it, and gives you a web interface to view and manage them.

## Run with Docker

```sh
docker compose up -d --build
```

Open http://localhost:8000 and create the admin account. Data lives in the `vegapunk-data` volume.

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
