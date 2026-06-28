## 1. Data Model And Contracts

- [x] 1.1 Add Alembic migration for ETF portfolio backtest runs, equity curves, trades, positions, benchmark metrics, and label outcome summaries.
- [x] 1.2 Add SQLAlchemy entities and Pydantic schemas for ETF portfolio backtest list, detail, run request, metrics, trades, holdings, and evidence summaries.
- [x] 1.3 Add rule/version metadata fields so each run records ranking version, allocation version, exit-rule version, fee assumptions, date range, and data coverage.
- [x] 1.4 Add repository helpers to create runs, append curve points, append trades, append position snapshots, mark failure, and load latest completed run.

## 2. Historical Replay Engine

- [x] 2.1 Extract or add pure calculation entry points that can compute ETF score, observation label, entry timing, and risk flags for a supplied `as_of_date` without reading latest-only snapshots.
- [x] 2.2 Implement historical ETF universe selection for each replay date using only daily data available on or before that date.
- [x] 2.3 Implement daily portfolio target generation using the existing risk-on/defensive/cash-wait allocation rules, 30% single-ETF cap, and data eligibility gates.
- [x] 2.4 Implement simulated execution using next available daily close or configured same-day close convention, with explicit fee assumptions and no intraday fills.
- [x] 2.5 Implement daily position valuation, cash accounting, equity curve, drawdown, turnover, holding-day, win-rate, and trade P/L calculations.
- [x] 2.6 Implement daily-granularity hard stop, trailing take-profit, trend weakening, take-profit watch, exit watch, and position sizing actions.
- [x] 2.7 Implement label outcome aggregation for strategy-used labels, including 1/3/5/10 trading-day forward returns where data exists.

## 3. Benchmarks And Guardrails

- [x] 3.1 Add cash-wait benchmark for the same date range.
- [x] 3.2 Add buy-and-hold or equal-weight ETF benchmark using eligible broad ETF data with explicit selection reason.
- [x] 3.3 Add sample insufficiency checks for short date ranges, low ETF coverage, missing close prices, and insufficient forward windows.
- [x] 3.4 Add strict future-data tests that fail if a replay date uses signal items, quote snapshots, or portfolio outputs generated after that replay date.

## 4. API And Admin Jobs

- [x] 4.1 Add authenticated `POST /api/short-research/etf-backtests` to start a manual ETF portfolio backtest.
- [x] 4.2 Add `GET /api/short-research/etf-backtests` to list recent runs with status, date range, and headline metrics.
- [x] 4.3 Add `GET /api/short-research/etf-backtests/{run_id}` to return metrics, curve, drawdown, trades, positions, benchmarks, and caveats.
- [x] 4.4 Add admin job entry for manual ETF portfolio backtest execution and job-run visibility.
- [x] 4.5 Ensure failed backtests return Chinese-readable error messages and never change real tracked positions or live strategy runs.

## 5. Frontend Integration

- [x] 5.1 Add a `/short-term` ETF historical backtest section that shows latest run status, run button, date range, data coverage, and caveats.
- [x] 5.2 Render strategy curve, drawdown curve, benchmark comparison, key metrics, and trade summary in compact Chinese UI.
- [x] 5.3 Show label evidence from the backtest without claiming labels are proven when sample count is insufficient.
- [x] 5.4 Clearly separate current ETF backtest evidence from legacy strategy-lab simulation and paper portfolio records.
- [x] 5.5 Show daily-backtest limitations: no minute-level intraday validation, no automatic trading, no guaranteed future returns.

## 6. Tests And Verification

- [x] 6.1 Add backend tests for replay date isolation, no future data usage, and no writes to tracked positions.
- [x] 6.2 Add backend tests for risk-on, defensive, and cash-wait portfolio replay behavior.
- [x] 6.3 Add backend tests for hard stop, trailing take-profit, trend weakening, take-profit watch, and exit-watch actions in daily replay.
- [x] 6.4 Add backend tests for metrics, benchmark comparison, and sample insufficiency outputs.
- [x] 6.5 Run `uv run pytest tests/test_short_research_api.py tests/test_short_research_jobs.py tests/test_tracked_positions.py` plus new backtest tests.
- [x] 6.6 Run `uv run ruff check .`.
- [x] 6.7 Run `corepack pnpm exec tsc --noEmit`.
- [x] 6.8 Run `corepack pnpm build:static`.

## 7. Deployment And Validation

- [x] 7.1 Deploy migration and code to the server.
- [x] 7.2 Run one short ETF portfolio backtest on the server using available historical ETF daily data.
- [x] 7.3 Verify `/short-term` shows the completed backtest and does not show old strategy simulation as current ETF proof.
- [x] 7.4 Verify backtest metrics, curves, benchmark, trades, and caveats are readable and internally consistent.
