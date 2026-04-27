PYTHON := uv run
PNPM := pnpm

.PHONY: db-upgrade db-downgrade db-seed backend-test backend-lint frontend-lint frontend-typecheck frontend-build

db-upgrade:
	cd backend && $(PYTHON) alembic upgrade head

db-downgrade:
	cd backend && $(PYTHON) alembic downgrade -1

db-seed:
	cd backend && $(PYTHON) alembic upgrade seed_default_data

backend-test:
	cd backend && $(PYTHON) pytest

backend-lint:
	cd backend && $(PYTHON) ruff check . && $(PYTHON) mypy app

frontend-lint:
	cd frontend && $(PNPM) lint

frontend-typecheck:
	cd frontend && $(PNPM) exec tsc --noEmit

frontend-build:
	cd frontend && $(PNPM) build
