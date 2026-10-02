# FundScope

FundScope is a single-user fund and ETF research dashboard for short-term research, manual position tracking, index valuation context, fund news, reminders, and explainable screening candidates.

## Single-user deployment

Each deployment is one personal instance: pages and APIs need no login, registration, or approval. All devices accessing that instance share its holdings and notification settings. Separate deployments must use separate databases and environment files; pulling code does not copy another instance's data or secrets.

Docker Compose uses PostgreSQL 16 in the persistent `postgres-data` volume. Without `DATABASE_URL`, the backend falls back to `./fundscope.db` (SQLite) in its working directory. SMTP credentials, including `SMTP_PASSWORD`, live in the root `.env`; the recipient and optional SMTP overrides are stored in the database and edited at `/settings/notifications`. The password input on that page is for testing only.

Compose binds to `127.0.0.1` by default. For a remote machine, use an SSH tunnel, a private network, or a proxy restricted to trusted clients. Set `FUNDSCOPE_BIND_ADDRESS` in `deploy/.env` only when you have configured that access boundary. See `deploy/README-ip.md` for an SSH tunnel example and `deploy/README-auth.md` for preserving an existing owner's settings.

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

## Short-Term Research

`/short-term` is the main product entry for short-term research:

- Default mode: exchange-traded ETFs for securities accounts, ranked with price trend, drawdown, volatility, data freshness, and turnover context.
- Optional mode: Alipay-style off-exchange funds, ranked with public NAV data and clear non-realtime NAV warnings.
- Manual tracking: record a fund or ETF you already bought, then receive email alerts when conservative exit, risk, trend-weakening, or profit-protection rules trigger.

The unified admin jobs are `daily_short_research_data`, `daily_short_research_signals`, `daily_short_research_advisor`, and `daily_tracked_position_alerts`. Recommendation and screening APIs remain available for compatibility, but they are no longer the primary product entry.

All outputs are research aids only. FundScope does not connect to Alipay or brokers, does not execute trades, does not provide trade commands, and does not guarantee future returns.

## Strategy Lab

FundScope includes a fund/ETF strategy lab for repeatable research. It stores strategy definitions, runs NAV-based backtests, starts local paper portfolios, and keeps the legacy fund screening workflow available as a strategy-lab view.

The first built-in templates are:

- ETF/fund momentum rotation with a valuation-percentile filter.
- Monthly DCA baseline for comparison.
- Fund screening using the existing recommendation scoring rules.

Open `/strategy-lab` to create strategies, run backtests, start a paper portfolio, or review screening results. Admin jobs also include `daily_strategy_paper` for refreshing active paper portfolios. Strategy Lab does not connect to brokers or place real orders.

## Portfolio Evidence

Generated MVP screenshots are stored in `docs/assets/screenshots/`:

- Portfolio: `docs/assets/screenshots/portfolio.png`
- Valuation: `docs/assets/screenshots/valuation.png`
- News: `docs/assets/screenshots/news.png`
- Recommendations: `docs/assets/screenshots/recommendations.png`
- Sample DCA email: `docs/assets/screenshots/sample-email.png`

These images use static frontend export output with mocked sample data, so they are suitable for README or resume portfolio material without exposing private account data.

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

- The application does not execute trades. It aggregates information, generates reminders, and ranks research candidates only.
- A personal instance has no application authentication. Restrict network access to its owner.
