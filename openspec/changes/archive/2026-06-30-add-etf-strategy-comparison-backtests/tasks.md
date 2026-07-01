## 1. Strategy Contract

- [x] 1.1 Audit current ETF资金配置参考, ETF portfolio backtest, and allocation modules to identify duplicated or divergent strategy rules.
- [x] 1.2 Add a shared ETF workbench strategy contract or equivalent service that returns ranking context, entry timing, portfolio mode, target weights, exclusion reasons, and strategy version.
- [x] 1.3 Update ETF资金配置参考 to call the shared strategy contract without changing existing API response fields.
- [x] 1.4 Update current ETF portfolio backtest to call the same shared strategy contract instead of maintaining separate allocation logic.
- [x] 1.5 Add tests proving a fixed historical date produces the same portfolio mode, target weights, exclusion reasons, and strategy version through page allocation and backtest paths.

## 2. Comparison Strategy Engine

- [x] 2.1 Add deterministic comparison strategy definitions for `current_workbench`, `momentum_top_n`, `momentum_volatility_weighted`, `momentum_regime_cash_filter`, and `equal_weight_benchmark`.
- [x] 2.2 Implement a comparison runner that replays all strategies over the same date range, fee assumptions, and ETF daily data snapshot.
- [x] 2.3 Ensure each strategy uses only data available on or before the replay date and records warm-up/sample-insufficient periods.
- [x] 2.4 Compute comparable metrics: cumulative return, max drawdown, volatility, return-to-drawdown ratio, trade count, turnover, win rate, cash-wait days, and data coverage.
- [x] 2.5 Add tests for deterministic output, data cut-off behavior, and sample-insufficient handling.

## 3. Persistence And APIs

- [x] 3.1 Add or extend storage for strategy comparison runs, per-strategy metrics, equity curves, and failure reasons while keeping real tracked positions unchanged.
- [x] 3.2 Add backend API to start a strategy comparison backtest for a date range and read latest/completed comparison results.
- [x] 3.3 Add admin job entry to run the default ETF strategy comparison manually.
- [x] 3.4 Ensure failures in one comparison strategy do not erase successful results from other strategies.
- [x] 3.5 Add tests for create/read comparison API and admin job behavior.

## 4. Frontend Evidence Display

- [x] 4.1 Add a compact strategy comparison evidence section to `/short-term` or the existing ETF backtest area.
- [x] 4.2 Show current strategy versus baseline strategies with date range, core metrics, data coverage, and sample warnings.
- [x] 4.3 Clearly label comparison output as historical daily simulation, not intraday validation, future prediction, or trading instruction.
- [x] 4.4 Ensure comparison evidence does not override current ranking labels, entry timing, quote price, portfolio weights, or tracked-position alerts.
- [x] 4.5 Add empty, loading, failed, and sample-insufficient states in Chinese.

## 5. Regression And Verification

- [x] 5.1 Run backend tests for ETF backtest, short research APIs/jobs, scheduler, and new comparison modules.
- [x] 5.2 Run `uv run ruff check .`.
- [x] 5.3 Run `corepack pnpm exec tsc --noEmit`.
- [x] 5.4 Run `corepack pnpm build:static`.
- [x] 5.5 Manually run one default comparison backtest and confirm `current_workbench` uses the same strategy version as ETF资金配置参考.
- [x] 5.6 Verify `/short-term` shows strategy comparison evidence without changing live recommendations or email alert rules.
