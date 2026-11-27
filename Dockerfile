# Build the frontend.
FROM node:24-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Run the server.
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /usr/local/bin/uv

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend/vegapunk ./vegapunk
RUN uv sync --locked --no-dev
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

CMD ["uvicorn", "vegapunk.main:app", "--host", "0.0.0.0", "--port", "8000"]
