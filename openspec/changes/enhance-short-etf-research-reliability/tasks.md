## 1. Data Model And Dependencies

- [x] 1.1 Add Alembic migration and SQLAlchemy models for ETF data health, short ETF reliability evaluations, and evaluation parameter items.
- [x] 1.2 Add `efinance` as a backend dependency and ensure Docker builds install it from the configured mirror.
- [x] 1.3 Add schemas for data health, retry results, reliability evaluation summaries, and evaluation detail rows.

## 2. Data Source Resilience

- [x] 2.1 Refactor short ETF data fetching into provider adapters for AKShare primary and efinance backup.
- [x] 2.2 Update ETF data sync to attempt backup provider per ETF when primary fetch fails or returns no usable rows.
- [x] 2.3 Persist per-ETF data health after each sync attempt, including provider, latest date, row counts, failure reason, and consecutive failure count.
- [x] 2.4 Add a retry-failed data sync service that targets failed or stale ETFs only.

## 3. Risk Signals And Review

- [x] 3.1 Expand deterministic risk labeling for chase risk, suspicious recent surge, high volatility, low liquidity, large drawdown, stale data, and insufficient history.
- [x] 3.2 Update short ETF signal generation so high-risk or insufficient-data ETFs are downgraded or excluded with readable research-only reasons.
- [x] 3.3 Strengthen rule-first review notes with data, trend, risk, opposing-view, and summary sections that preserve original rank and score.
- [x] 3.4 Add tests proving signal and review outputs still omit buy/sell instructions, target prices, and guaranteed-return fields.

## 4. Reliability Evaluation

- [x] 4.1 Implement reliability evaluation service using historical replay, fees, baseline comparison, and nearby parameter combinations.
- [x] 4.2 Persist evaluation summaries and parameter-level results without modifying signal runs, paper portfolios, or virtual orders.
- [x] 4.3 Enforce sample-size warnings and parameter-instability flags in evaluation conclusions.
- [x] 4.4 Add backend tests for evaluation persistence, sample-insufficient conclusions, fee impact, and parameter stability risk.

## 5. APIs And Jobs

- [x] 5.1 Add `GET /api/short-etf/data-status` and `POST /api/short-etf/data/retry-failed`.
- [x] 5.2 Add `POST /api/short-etf/evaluations`, `GET /api/short-etf/evaluations`, and `GET /api/short-etf/evaluations/{id}`.
- [x] 5.3 Add admin job hooks for short ETF retry-failed data sync and scheduled reliability evaluation if enabled.
- [x] 5.4 Add API tests for data health, fallback reporting, retry-failed sync, evaluation creation, and evaluation retrieval.

## 6. Frontend

- [x] 6.1 Add short ETF data-health cards and provider fallback/failure details to `/strategy-lab?view=short-etf`.
- [x] 6.2 Add retry-failed ETF controls to Strategy Lab and Admin Jobs with Chinese success/failure states.
- [x] 6.3 Add reliability evaluation controls, summary cards, parameter stability table, fee impact, drawdown, and baseline comparison charts.
- [x] 6.4 Update Chinese copy to explain public-data delay, sample insufficiency, ETF T+1 constraints, and research-only warnings.

## 7. Verification And Deployment

- [x] 7.1 Run `uv run pytest`, `uv run ruff check .`, and `uv run mypy app`.
- [x] 7.2 Run `corepack pnpm lint`, `corepack pnpm exec tsc --noEmit`, and `corepack pnpm build:static`.
- [x] 7.3 Deploy to the IP server, run Alembic migration, rebuild containers, and verify short ETF data status and evaluation pages.
- [x] 7.4 Manually run data sync, retry-failed sync, signal generation, review generation, reliability evaluation, and paper update from the web UI.
