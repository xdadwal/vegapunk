# Shortcuts for the commands in docs/development.md. Run `make` to list them.
# Written for GNU make 3.81 (the macOS default) and later.

.DEFAULT_GOAL := help

.PHONY: help setup backend frontend test test-backend test-frontend lint format \
	format-check coverage build check smoke up down clean

help: ## List the targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "}; {printf "  %-14s %s\n", $$1, $$2}'

setup: ## Install backend and frontend dependencies from the lockfiles
	cd backend && uv sync --locked
	cd frontend && npm ci

backend: ## Run the API with reload on http://localhost:8000
	cd backend && uv run uvicorn vegapunk.main:app --reload

frontend: ## Run the Vite dev server on http://localhost:5173 (proxies /api)
	cd frontend && npm run dev

test: test-backend test-frontend ## Run the backend and frontend tests

test-backend: ## Run the backend tests
	cd backend && uv run pytest

test-frontend: ## Run the frontend tests
	cd frontend && npm test

lint: ## Lint the backend (ruff) and the frontend (oxlint)
	cd backend && uv run ruff check .
	cd frontend && npm run lint

format: ## Format the backend (ruff) and the frontend (Prettier)
	cd backend && uv run ruff format .
	cd frontend && npm run format

format-check: ## Check formatting without changing files
	cd backend && uv run ruff format --check .
	cd frontend && npm run format:check

coverage: ## Run both test suites with coverage (fails under the floor)
	cd backend && uv run pytest --cov --cov-report=term-missing --cov-report=xml
	cd frontend && npm run test:coverage

build: ## Type-check and build the frontend into frontend/dist
	cd frontend && npm run build

check: ## Run what CI's backend and frontend jobs run
	cd backend && uv sync --locked
	cd backend && uv run ruff check .
	cd backend && uv run ruff format --check .
	cd backend && uv run pytest --cov --cov-report=term-missing --cov-report=xml
	cd frontend && npm ci
	cd frontend && npm run lint
	cd frontend && npm run format:check
	cd frontend && npm run test:coverage
	cd frontend && npm run build

smoke: ## Build the image and check it starts healthy (needs Docker)
	sh scripts/smoke.sh

up: ## Build and start the app with Docker Compose on port 8000
	docker compose up -d --build

down: ## Stop and remove the app's container (keeps the data volume)
	docker compose down

clean: ## Remove build output, coverage reports and caches (not data or dependencies)
	rm -rf frontend/dist frontend/coverage
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/htmlcov
	rm -f backend/.coverage backend/coverage.xml
