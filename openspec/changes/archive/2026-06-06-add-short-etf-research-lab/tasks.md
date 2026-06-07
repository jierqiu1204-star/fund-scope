## 1. Data Model And Defaults

- [x] 1.1 Add Alembic migration for ETF universe, ETF price history, ETF metrics, ETF theme exposures, signal runs, signal items, paper portfolios, paper orders, and paper equity curve.
- [x] 1.2 Add SQLAlchemy models and exports for the new short ETF tables.
- [x] 1.3 Add a default high-liquidity ETF universe with theme labels, exchange, trading rule label, and short-term eligibility metadata.
- [x] 1.4 Add idempotent startup or admin seeding logic for the default ETF universe.

## 2. ETF Data Sync And Metrics

- [x] 2.1 Add `short_etf` service module for ETF market data fetching through AKShare-backed adapters.
- [x] 2.2 Implement idempotent daily ETF price sync with per-ETF failure collection.
- [x] 2.3 Compute deterministic metrics for 5/20/60 day return, average turnover, volatility, max drawdown, trend score, liquidity score, and overextension risk.
- [x] 2.4 Add backend tests for duplicate sync prevention, partial data-source failure handling, low liquidity flags, and chase-risk flags.

## 3. Signal Generation And Review

- [x] 3.1 Implement short ETF signal run generation with ranked items, score breakdowns, risk flags, theme labels, and observation-oriented conclusions.
- [x] 3.2 Enforce exclusion of one-year holding, closed-period, fixed-holding, and off-exchange-only products from short ETF signals.
- [x] 3.3 Implement rule-first multi-role review for short ETF signal runs without changing ranks or scores.
- [x] 3.4 Add backend tests proving prohibited buy/sell language and target-price fields are absent.

## 4. Short ETF Paper Trading

- [x] 4.1 Implement short ETF paper portfolio creation with configurable initial cash and active status.
- [x] 4.2 Implement daily paper run logic that creates virtual orders, positions, cash, equity curve, drawdown, and summary metrics.
- [x] 4.3 Enforce T+1 sell restriction for stock-style ETFs in the paper engine.
- [x] 4.4 Add backend tests for T+1 restriction, equity curve output, drawdown calculation, and order persistence.

## 5. APIs And Jobs

- [x] 5.1 Add `/api/short-etf/universe`, `/api/short-etf/data/sync`, `/api/short-etf/signals/run`, and `/api/short-etf/signals/latest`.
- [x] 5.2 Add `/api/short-etf/paper/start`, `/api/short-etf/paper/{paper_id}/run`, and `/api/short-etf/paper/{paper_id}`.
- [x] 5.3 Add admin job hooks for ETF data sync, short ETF signal generation, and active short ETF paper updates.
- [x] 5.4 Add API tests for success, empty data, data-source failure, and not-found states.

## 6. Frontend

- [x] 6.1 Extend strategy lab view routing with `short-etf` and add Chinese navigation label `短线 ETF`.
- [x] 6.2 Build data preparation controls, ETF data status, latest signal cards, risk labels, and review report UI.
- [x] 6.3 Build short ETF paper trading charts for equity, drawdown, current positions, and recent virtual orders.
- [x] 6.4 Add clear Chinese copy explaining ETF short-term simulation, T+1/T+0 simplification, and no real trading.

## 7. Verification And Deployment

- [x] 7.1 Run `uv run pytest`, `uv run ruff check .`, and `uv run mypy app`.
- [x] 7.2 Run `corepack pnpm lint`, `corepack pnpm exec tsc --noEmit`, and `corepack pnpm build:static`.
- [x] 7.3 Deploy to the IP server with Docker Compose, run Alembic migration, and verify `/strategy-lab?view=short-etf`.
- [x] 7.4 Manually run ETF data sync, signal generation, review generation, and paper update from the web UI.
