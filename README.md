# Vegapunk

An open-source tool for managing a homelab host and its services.

> **Status:** Early development — things will change quickly.

## Overview

Vegapunk is a single server that runs on your homelab host. It periodically captures data about the host and the services running on it, and gives you a web interface to view and manage them.

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
