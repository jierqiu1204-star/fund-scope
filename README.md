# FundScope

FundScope is a single-user fund-investing dashboard for tracking transactions, monitoring index valuations, reviewing fund news, and receiving monthly DCA reminders.

## Stack

- Backend: FastAPI, SQLAlchemy async, Alembic, APScheduler
- Frontend: Next.js App Router, TypeScript, Tailwind CSS, React Query, Recharts
- Infra: Docker Compose, Postgres, nginx, Certbot, GitHub Actions

## Repository Layout

- `backend/`: API server, scheduler, data ingestion, tests, and migrations
- `frontend/`: static-exported Next.js UI
- `deploy/`: docker-compose, nginx, backup, and VPS deployment docs
- `openspec/`: change proposal, design, specs, and implementation tasks

## Quick Start

### Local Development

FundScope's default local-development path is intentionally close to production:

- Run a local PostgreSQL instance before starting the app.
- The root `.env` file is used by the backend app and Alembic migrations.
- Frontend environment variables are loaded from files inside `frontend/`, not from the repository root.

1. Copy `.env.example` to `.env` and adjust the PostgreSQL connection, SMTP settings, and LLM credentials.
2. Start the backend:

   ```bash
   cd backend
   uv sync --extra dev
   uv run alembic upgrade head
   uv run uvicorn app.main:app --reload
   ```

3. Start the frontend in a second terminal:

   ```bash
   cd frontend
   corepack pnpm install
   corepack pnpm dev
   ```

4. Open `http://localhost:3000`. The frontend talks to `http://localhost:8000` by default.
5. Use `corepack pnpm build` for local frontend validation. Use `corepack pnpm build:static` only when you need the static export artifacts used by deployment.

### Local Port Overrides

If port `3000` is already in use, run the frontend on another port and allow that origin in the backend:

```bash
cd frontend
corepack pnpm dev -- --port 3100
```

Then update the root `.env` file and restart the backend:

```env
CORS_ORIGINS=http://localhost:3100
```

If the backend is not reachable at `http://localhost:8000`, copy `frontend/.env.local.example` to `frontend/.env.local` and set the actual API base URL there:

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8001
```

Changing `frontend/.env.local` requires restarting the frontend dev server.

## Tooling

- Backend lint/typecheck: `uv run ruff check .` and `uv run mypy app`
- Backend tests: `uv run pytest`
- Frontend lint/typecheck/build: `pnpm lint`, `pnpm exec tsc --noEmit`, `pnpm build`
- Frontend static export for deployment: `pnpm build:static`
- Pre-commit hooks: `pre-commit install`

## Architecture

```text
                        +-----------------------+
                        |      FundScope UI     |
                        |   Next.js static app  |
                        +-----------+-----------+
                                    |
                                    v
                        +-----------------------+
                        |     FastAPI Backend   |
                        | routes + scheduler    |
                        +-----+-----------+-----+
                              |           |
                              v           v
                   +----------------+   +-------------------+
                   |   PostgreSQL   |   | External Sources  |
                   | app + job data |   | AKShare / RSS/LLM |
                   +----------------+   +-------------------+
```

## Notes

- The application does not execute trades. It aggregates information and generates reminders only.
- nginx basic auth is the primary authentication layer for the deployed site.
