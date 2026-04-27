# Tasks: add-fundscope-mvp

## 1. Project scaffolding & tooling

- [x] 1.1 Initialize `backend/` Python project with `pyproject.toml` (Python 3.11+, uv or poetry), dependencies: fastapi, sqlalchemy[asyncio], asyncpg, alembic, apscheduler, pydantic-settings, httpx, akshare, aiosmtplib, jinja2, openai, beautifulsoup4, pytest, pytest-asyncio, ruff, mypy
- [x] 1.2 Initialize `frontend/` with `create-next-app` (TypeScript, App Router, Tailwind), add dependencies: recharts, zod, @tanstack/react-query, axios
- [x] 1.3 Add `.gitignore` entries for `.env`, `__pycache__`, `node_modules`, `.next`, `*.db`
- [x] 1.4 Add root `README.md` with project overview, setup, and architecture diagram placeholder
- [x] 1.5 Configure `ruff` + `mypy` for backend and `eslint` + `prettier` for frontend; add pre-commit hooks
- [x] 1.6 Create `.env.example` with all required environment variables documented (`DATABASE_URL`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `MODEL_NAME`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `NGINX_BASIC_AUTH_USER`, `NGINX_BASIC_AUTH_PASS`)

## 2. Database models & migrations

- [x] 2.1 Define SQLAlchemy models per design D3: `users`, `funds`, `fund_nav_history`, `indices`, `index_valuation_history`, `portfolios`, `transactions`, `holdings_snapshot`, `news_items`, `news_summaries`, `notification_log`, `job_runs`
- [x] 2.2 Configure Alembic with async engine support and autogenerate
- [x] 2.3 Generate initial migration from models
- [x] 2.4 Write seed migration that inserts default watchlist (6 indices) and default fund list (4 funds per design D8) with target allocations
- [x] 2.5 Add `make db-upgrade` / `db-downgrade` / `db-seed` Makefile or task-runner shortcuts

## 3. Core application bootstrap

- [x] 3.1 Implement `backend/app/core/config.py` (pydantic Settings) to load `.env`
- [x] 3.2 Implement `backend/app/core/db.py` (async SessionLocal, dependency injection for routes)
- [x] 3.3 Implement `backend/app/main.py` with FastAPI app, CORS, exception handlers, and lifespan startup that launches APScheduler
- [x] 3.4 Implement `backend/app/core/security.py` (optional extra layer; primary auth is at nginx; keep an allow-all stub here)
- [x] 3.5 Add `/health` readiness endpoint returning app + DB status

## 4. Shared fund data ingestion service

- [x] 4.1 Implement `services/fund_data.py`: `fetch_fund_nav(code, from_date, to_date)` via AKShare with httpx fallback to 天天基金
- [x] 4.2 Implement `services/index_data.py`: `fetch_index_valuation(index_code, date)` returning PE / PB / dividend yield
- [x] 4.3 Implement retry policy (2 retries with exponential backoff) and structured logging for all external calls
- [ ] 4.4 Add unit tests with recorded VCR cassettes for AKShare and fallback parsers

## 5. Scheduler & job infrastructure

- [x] 5.1 Implement `services/scheduler.py` wiring APScheduler to run registered jobs
- [x] 5.2 Implement `services/job_runner.py` base class that writes `job_runs` rows (start, end, status, error) for every job
- [x] 5.3 Register `daily_fund_nav` job (19:00) using `fund_data` service for all funds in watchlist + holdings
- [x] 5.4 Register `daily_valuation` job (19:15) using `index_data` service for all watchlist indices, computing 10-year rolling percentile per row
- [x] 5.5 Register `daily_holdings_snapshot` job (19:20) aggregating latest NAV × shares into `holdings_snapshot` (idempotent upsert)
- [x] 5.6 Register `daily_news_fetch` job (19:30) - implementation under §8
- [x] 5.7 Register `monthly_dca_reminder` job (cron: day 1, 09:00) - implementation under §7
- [x] 5.8 Expose `POST /api/admin/jobs/{job_name}/run` for manual trigger; wire each job into an admin router

## 6. Feature A - portfolio-tracking

- [x] 6.1 Implement `POST /api/transactions` with validation: reject unknown fund codes, compute shares from amount/nav/fee (spec: "Record fund transactions")
- [x] 6.2 Implement `GET /api/transactions` (list, paginated)
- [x] 6.3 Implement `GET /api/portfolio/holdings` returning current aggregated holdings from latest `holdings_snapshot` (spec: "Compute aggregated holdings")
- [x] 6.4 Implement stale-data detection: return an `is_stale: true` flag when latest NAV > 2 business days old
- [x] 6.5 Implement `GET /api/portfolio/value-history` returning daily portfolio total-value series
- [x] 6.6 Implement `POST /api/transactions/import-csv` with atomic validation and per-row error reporting (spec: "Support CSV import")
- [x] 6.7 Build frontend `/portfolio` page: holdings cards, value-history chart (Recharts Area), allocation donut, empty-state onboarding CTA
- [x] 6.8 Build frontend `/transactions` page: table + add-transaction modal + CSV upload
- [ ] 6.9 Write pytest tests covering every scenario in `portfolio-tracking/spec.md`

## 7. Feature B - valuation-monitoring

- [x] 7.1 Implement `GET /api/valuation/current` returning latest PE/PB + percentile per watchlist index
- [x] 7.2 Implement `GET /api/valuation/{index_code}/history?from=...&to=...`
- [x] 7.3 Implement percentile calculation utility with 10-year rolling window + handling for insufficient history (flag effective-window length)
- [x] 7.4 Build frontend `/valuation` page: cards per index with PE/PB, percentile bar (green/neutral/red), data-as-of date
- [x] 7.5 Build frontend `/valuation/{code}` detail page with PE/PB toggle line chart
- [x] 7.6 Implement backfill command `python -m app.cli backfill-valuation --index CSI300 --years 10` for first-time setup
- [ ] 7.7 Write pytest tests covering every scenario in `valuation-monitoring/spec.md`

## 8. Feature D - news-aggregation

- [x] 8.1 Implement news fetcher `services/news.py` pulling 东方财富 fund-level RSS/API (primary); skip duplicates by URL
- [x] 8.2 Implement `services/llm.py` wrapping `openai` SDK with `OPENAI_BASE_URL` + `MODEL_NAME` from settings
- [x] 8.3 Author summary prompt in `backend/app/services/prompts/news_summary.txt` matching spec (≤80 中文字符, event type + fact only, no advice)
- [x] 8.4 Implement `services/news_summarizer.py` parsing LLM output into `summary` + `event_type` (dividend / manager_change / size_change / strategy_change / other)
- [x] 8.5 Wire `daily_news_fetch` job to call fetcher → summarizer → persist; LLM failures leave raw news_item intact per spec
- [x] 8.6 Implement `POST /api/admin/jobs/news_summary_backfill/run` to retry failed summaries
- [x] 8.7 Implement `GET /api/news?fund_code=&event_type=&days=14` with filters
- [x] 8.8 Build frontend `/news` page: grouped by fund, last-14-days per group, event-type badge + filter, fallback to title-only when summary missing
- [x] 8.9 Write pytest tests covering every scenario in `news-aggregation/spec.md` (use LLM stub that returns canned output)

## 9. Feature C - investment-reminders

- [x] 9.1 Implement `services/dca_calculator.py` with percentile-band rules (spec: "Compute dynamic DCA amount") and test all 5 percentile bands including missing-data fallback
- [x] 9.2 Implement `services/notifier.py` wrapping aiosmtplib + Jinja2 templates with retry + notification_log audit
- [x] 9.3 Author `templates/emails/base.html.j2`, `dca_monthly.html.j2`, `job_failure.html.j2`
- [x] 9.4 Implement `GET /api/settings/notifications` + `PUT /api/settings/notifications` with SMTP test-send endpoint
- [x] 9.5 Implement `monthly_dca_reminder` job body: compute amount → compose per-fund breakdown by target allocation → include highlighted critical-events section (manager_change / strategy_change from news-aggregation) → send via notifier
- [x] 9.6 Build frontend `/settings/notifications` page with SMTP fields + base-amount + reference-index picker + "Send test email" button
- [x] 9.7 Build frontend `/admin/jobs` page listing recent `job_runs` with status + manual re-trigger button
- [x] 9.8 Write pytest tests covering every scenario in `investment-reminders/spec.md`

## 10. Onboarding & default seed

- [x] 10.1 Implement `POST /api/onboarding/apply-default-portfolio` that seeds watchlist with default funds per design D8; require `force=true` when non-empty watchlist exists
- [x] 10.2 Build frontend `/onboarding` page with "Apply default" button + merge/replace confirmation flow
- [x] 10.3 Redirect new users (detected by empty `transactions` + default watchlist) to `/onboarding` on first visit

## 11. Deployment infrastructure

- [x] 11.1 Write `backend/Dockerfile` (multi-stage, python:3.11-slim base, non-root user)
- [x] 11.2 Write `frontend/Dockerfile` as build-only stage that produces static files in `/app/out` (using `next build` + `next export`)
- [x] 11.3 Write `deploy/docker-compose.yml` with services: `backend`, `frontend-static-builder` (one-shot), `postgres`, `nginx`, `certbot`
- [x] 11.4 Write `deploy/nginx.conf`: serve static frontend from shared volume, proxy `/api` to backend, basic auth on whole site, HTTPS with certbot-managed certs
- [x] 11.5 Write `deploy/certbot-init.sh` for first-time Let's Encrypt certificate issuance
- [x] 11.6 Write `deploy/backup.sh` cron script for daily `pg_dump` to `/var/backups/fundscope/`, keep 7 days
- [x] 11.7 Document full VPS setup in `deploy/README.md` (per design D10 Migration Plan)

## 12. CI / CD

- [x] 12.1 Write `.github/workflows/ci.yml` running backend pytest + ruff + mypy and frontend lint + typecheck + build on PRs
- [x] 12.2 Write `.github/workflows/deploy.yml` triggered on `main` push: SSH to VPS, `git pull`, `docker compose up -d --build`
- [ ] 12.3 Configure GitHub Secrets: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`

## 13. End-to-end verification

- [ ] 13.1 Spin up full stack locally via `docker compose up`; verify each feature A/B/C/D produces expected UI output
- [ ] 13.2 Record a real transaction -> confirm it flows into holdings -> value-history chart updates after snapshot job run
- [ ] 13.3 Manually trigger `daily_valuation` -> spot-check one index's PE against the official CSIndex website
- [ ] 13.4 Manually trigger `monthly_dca_reminder` -> receive email -> verify amounts match percentile-band rules across all 5 bands (adjust reference index or mock percentile for testing)
- [ ] 13.5 Manually trigger `daily_news_fetch` -> verify `/news` shows summaries + event-type badges; disable LLM endpoint briefly to verify raw-fallback path
- [ ] 13.6 Deploy to VPS, verify HTTPS, basic auth, and that scheduler fires at configured times (temporarily set to next 2 minutes to observe)
- [ ] 13.7 Take screenshots of `/portfolio`, `/valuation`, `/news`, sample email for README / resume portfolio
