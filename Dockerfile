# Build the frontend.
FROM node:24-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Install the backend's dependencies. This stage has git because one dependency (logpose)
# is installed from a git tag; the final image does not need it.
FROM python:3.12-slim AS backend
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/vegapunk ./vegapunk
RUN uv sync --locked --no-dev

# Run the server.
FROM python:3.12-slim
WORKDIR /app
COPY --from=backend /app /app
COPY --from=frontend /build/dist /app/static

RUN useradd --system --uid 10001 vegapunk \
    && mkdir /data \
    && chown vegapunk /data
USER vegapunk

ENV PATH="/app/.venv/bin:$PATH" \
    VEGAPUNK_DATA_DIR=/data \
    VEGAPUNK_STATIC_DIR=/app/static
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]

# One worker only: capture runs in a thread inside this process and keeps the previous
# CPU readings in memory, so a second worker would capture everything twice.
# On stop, uvicorn waits for open requests before the app shuts down, and a browser tab
# following an answer stays open until the answer ends; after 3 s it cuts them off, then
# the app stops the running answers. compose.yaml gives Docker's stop the time for both.
CMD ["uvicorn", "vegapunk.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--timeout-graceful-shutdown", "3"]
