## Why

Current ETF portfolio backtests show long cash-wait periods partly because daily ETF history is only backfilled for a short recent window and partly because the allocation rule treats "full ETF capital may be used" as "must generate a near-full allocation." This makes the result hard to read: the system can look like it failed to find data when it is actually waiting because too few assets pass risk gates.

This change separates the two concerns: pull longer verified ETF daily history for research/backtests, and change ETF allocation from "force full exposure" to "cap exposure at 100%, invest only in qualified candidates, leave the rest as explicit waiting cash."

## What Changes

- Add ETF daily-history backfill jobs and admin/API entry points for `365 / 730 / 1095` day ranges.
- Keep daily scheduled ETF refresh lightweight, but allow manual and scheduled long-history refresh for research/backtest readiness.
- Change ETF observation allocation semantics:
  - `100%` means maximum allowed ETF exposure, not mandatory exposure.
  - Qualified candidates receive weights up to the existing `30%` single-ETF cap.
  - If fewer than four qualified candidates exist, allocate partial ETF exposure instead of returning all-cash solely because candidate count is below four.
  - Remaining capital is shown as "waiting cash" with explicit reasons.
- Preserve strict data reliability rules:
  - Only verified or alternate-provider daily data can enter backtests and portfolio weights.
  - Stale, estimated, or display-only values remain excluded from decision weights.
- Update ETF portfolio backtest reporting:
  - Distinguish requested range, available data range, warm-up period, first signal date, and first trade date.
  - Attribute cash-wait days to data warm-up, candidate shortage, or market/risk filter reasons.
- Include push and deployment as implementation completion tasks:
  - commit with a concise Chinese message,
  - push to Gitee and GitHub,
  - deploy to `110.42.222.9`,
  - run the long-history backfill and regenerate ETF signals/portfolio/backtest.

## Capabilities

### New Capabilities
- `etf-history-backfill`: Provides long-range ETF daily history backfill for research, portfolio generation, label validation, and backtesting.

### Modified Capabilities
- `etf-observation-portfolio-optimization`: Change exposure semantics from forced full allocation to maximum exposure with explicit partial allocation and waiting cash.
- `etf-portfolio-backtest`: Report effective data coverage and cash-wait attribution, and benefit from longer verified ETF history.
- `market-data-reliability`: Clarify that long-history backfill data is decision-eligible only when source/date/reliability checks pass.
- `short-term-research`: Surface waiting-cash reasons and long-history readiness in the short-term ETF workbench.

## Impact

- Backend services:
  - ETF daily-history sync jobs and admin job registry.
  - ETF observation portfolio optimizer.
  - ETF portfolio backtest coverage/cash-wait reporting.
  - Short-term research status/data readiness responses.
- Frontend:
  - `/short-term` ETF资金配置参考 wording and waiting-cash explanation.
  - Backtest/result cards showing requested range, effective range, warm-up, first signal, and first trade.
  - Admin jobs page showing long-history backfill actions and results.
- Database:
  - No breaking schema changes expected.
  - Existing `etf_price_history` is reused with verified source metadata/health where available.
- Deployment:
  - After implementation, push to both remotes and deploy to `110.42.222.9`.
  - Run `730` day ETF backfill first, then regenerate ETF signals, observation portfolio, and ETF portfolio backtest.
