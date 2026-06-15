## Why

Short-term ETF research currently works end to end, but it depends on a single public data source and the risk review is still basic. For a finance-focused workflow, the system needs stronger data-source resilience, clearer risk evidence, and historical reliability checks before users treat signals as worth observing.

## What Changes

- Add a backup ETF data-source path so failed AKShare requests can be retried through a secondary public provider.
- Track per-ETF data health, including latest data date, provider used, failure reason, and consecutive failure count.
- Expand short-term ETF risk labels for chase-risk, high volatility, weak liquidity, data gaps, large drawdown, and suspicious recent surge patterns.
- Add reliability evaluation for short-term ETF signals, including parameter stability, historical replay, fee impact, and sample-size warnings.
- Improve the Chinese web UI for data health, retry actions, risk explanations, reliability charts, and beginner-friendly warnings.
- Keep all outputs as research observation; no real trading, no broker integration, no buy/sell instruction, and no target-price output.

## Capabilities

### New Capabilities
- `short-etf-research-reliability`: Data-source resilience, data health reporting, expanded risk review, and reliability evaluation for the short-term ETF research workflow.

### Modified Capabilities

## Impact

- Backend short ETF data, signal, review, paper, jobs, and API modules.
- New optional dependency on a public backup data library such as `efinance`.
- Strategy Lab short ETF UI and admin jobs UI.
- Tests for data-source fallback, failure reporting, risk labeling, reliability evaluation, and no-trading-language guarantees.
