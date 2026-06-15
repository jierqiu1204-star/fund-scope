## 1. ETF Universe Data Layer

- [x] 1.1 Add an ETF universe discovery service that fetches public exchange ETF metadata and normalizes code, name, exchange, category, trading rule, and source fields.
- [x] 1.2 Upsert discovered ETFs into `tradable_etfs` without duplicating existing records and preserve current manual theme labels where present.
- [x] 1.3 Implement eligibility rules that exclude money market ETFs, LOF products, closed products, holding-period products, and off-exchange funds from default short-term ETF display.
- [x] 1.4 Add data-quality fields or derived flags for default display eligibility, including latest data date, usable history days, average turnover, stale data, and consecutive sync failures.

## 2. Sync And Scoring

- [x] 2.1 Add a web-runnable `daily_etf_universe` admin job that refreshes the ETF universe and returns discovered, inserted, updated, excluded, default-display, and failed counts.
- [x] 2.2 Update short research ETF data sync to process the dynamic ETF universe in bounded batches and record progress, failures, and skipped ETFs.
- [x] 2.3 Prioritize sync order so tracked ETFs, default-display ETFs, and high-turnover ETFs run before low-priority ETF records.
- [x] 2.4 Update ETF scoring to include data quality while preserving deterministic trend, risk, liquidity, drawdown, volatility, and overextension logic.
- [x] 2.5 Add duplicate theme/index de-noising so the default list prefers the most liquid and data-complete ETF among highly similar candidates.

## 3. APIs And Observation Portfolio

- [x] 3.1 Extend `/api/short-research/status` with ETF total, eligible, default-display, data-stale, and failed counts.
- [x] 3.2 Extend `/api/short-research/assets` with `universe=default|all|illiquid` for ETF mode while keeping fund results separate.
- [x] 3.3 Ensure `/api/short-research/assets/etf/{code}` can return details for dynamically discovered ETFs not present in the old default constants.
- [x] 3.4 Add a rule-based ETF observation portfolio API that returns target ETF weights, cash weight, score evidence, risk reasons, and data date.
- [x] 3.5 Ensure AI advisor generation can explain dynamic ETF candidates without changing ranking, score, labels, or observation portfolio weights.

## 4. Frontend

- [x] 4.1 Update `/short-term` ETF mode to show full ETF universe counts, default selected counts, latest data date, and data-quality warnings.
- [x] 4.2 Add ETF universe filter controls for default selected, all analyzable, and low-liquidity-inclusive views.
- [x] 4.3 Show default-display exclusion reasons such as low turnover, stale data, insufficient history, or repeated sync failure.
- [x] 4.4 Add the ETF observation portfolio card with target weights, cash weight, evidence, risk reasons, and research-only wording.
- [x] 4.5 Keep Alipay off-exchange fund mode unchanged except for avoiding mixed ETF universe state.

## 5. Tests And Validation

- [x] 5.1 Add backend tests for universe refresh idempotency, exclusion rules, dynamic ETF detail lookup, and count reporting.
- [x] 5.2 Add backend tests for batched ETF sync priority, partial failures, data-health updates, and `universe` API filtering.
- [x] 5.3 Add backend tests for observation portfolio weights, cash increase under high risk, and absence of trade instruction fields.
- [x] 5.4 Add frontend type/build checks and targeted UI tests or component assertions for ETF universe filters and observation portfolio display.
- [x] 5.5 Run regression commands: `uv run pytest`, `uv run ruff check .`, `uv run mypy app`, `corepack pnpm exec tsc --noEmit`, and `corepack pnpm build:static`.
