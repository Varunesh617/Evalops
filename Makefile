.DEFAULT_GOAL := help
SHELL := /bin/bash

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------
PYTHON  := python3
PIP     := pip
VENV    := .venv
SRC     := backend
TESTS   := tests

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

.PHONY: install
install: ## Create venv and install all dependencies
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/$(PIP) install --upgrade pip
	$(VENV)/bin/$(PIP) install -e ".[dev,test]"

.PHONY: install-prod
install-prod: ## Install production dependencies only
	$(VENV)/bin/$(PIP) install -e "."

# ---------------------------------------------------------------------------
# Code Quality
# ---------------------------------------------------------------------------

.PHONY: lint
lint: ## Run linter (ruff check + format check)
	$(VENV)/bin/ruff check $(SRC)/ $(TESTS)/
	$(VENV)/bin/ruff format --check $(SRC)/ $(TESTS)/

.PHONY: format
format: ## Auto-format code
	$(VENV)/bin/ruff check --fix $(SRC)/ $(TESTS)/
	$(VENV)/bin/ruff format $(SRC)/ $(TESTS)/

.PHONY: typecheck
typecheck: ## Run mypy type checking
	$(VENV)/bin/mypy $(SRC)/ --ignore-missing-imports

.PHONY: security
security: ## Run bandit + safety security scans
	$(VENV)/bin/bandit -r $(SRC)/ --severity-level medium
	$(VENV)/bin/safety check

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

.PHONY: test
test: ## Run all tests
	$(VENV)/bin/pytest $(TESTS)/ -v

.PHONY: test-unit
test-unit: ## Run unit tests with coverage
	$(VENV)/bin/pytest $(TESTS)/unit/ -v \
		--cov=$(SRC) --cov-report=term-missing --cov-report=html

.PHONY: test-integration
test-integration: ## Run integration tests
	$(VENV)/bin/pytest $(TESTS)/integration/ -v

.PHONY: test-e2e
test-e2e: ## Run end-to-end tests
	$(VENV)/bin/pytest $(TESTS)/e2e/ -v

.PHONY: coverage
coverage: ## Generate coverage report
	$(VENV)/bin/pytest $(TESTS)/ \
		--cov=$(SRC) --cov-report=html --cov-report=xml
	@echo "Open htmlcov/index.html to view the report"

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

.PHONY: docker-build
docker-build: ## Build Docker images
	docker build -t evalops-backend:latest -f docker/Dockerfile.backend .

.PHONY: docker-up
docker-up: ## Start full stack with docker compose
	docker compose -f docker/docker-compose.yml up -d

.PHONY: docker-down
docker-down: ## Stop all services
	docker compose -f docker/docker-compose.yml down

.PHONY: docker-logs
docker-logs: ## Tail service logs
	docker compose -f docker/docker-compose.yml logs -f

# ---------------------------------------------------------------------------
# Development
# ---------------------------------------------------------------------------

.PHONY: run
run: ## Start the dev server
	$(VENV)/bin/uvicorn backend.api.app:app --reload --host 0.0.0.0 --port 8000

.PHONY: run-prod
run-prod: ## Start the production server
	$(VENV)/bin/uvicorn backend.api.app:app --host 0.0.0.0 --port 8000 --workers 4

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

.PHONY: db-migrate
db-migrate: ## Run database migrations
	$(VENV)/bin/alembic upgrade head

.PHONY: db-revision
db-revision: ## Create a new migration revision
	$(VENV)/bin/alembic revision --autogenerate -m "$(msg)"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

.PHONY: clean
clean: ## Remove build artifacts and caches
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache
	rm -rf htmlcov .coverage coverage.xml
	rm -rf build dist *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

.PHONY: help
help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'
