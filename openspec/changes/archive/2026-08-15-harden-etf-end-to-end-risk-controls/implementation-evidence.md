# Implementation evidence

## Implemented controls

- ETF action emails fail closed unless the same prepared evaluation contains a fresh, explicitly decision-eligible intraday quote with finite ordered bid/ask. Daily ETF checks do not fall back to a close or legacy quote; confirmed fund NAV behavior is unchanged.
- Alert and audit context records sell-side bid execution evidence, spread, signal-to-bid gap, stop gap, fixed slippage reserve, stable unavailable reason, and `automatic_execution=false`.
- Daily research backtests use the existing next-eligible-session total-return-adjusted open selector, 100-share lot rounding, frozen 5 bps fee plus 5 bps base slippage per side, and a separately reported 20 bps stress scenario. Missing or unprovable execution remains pending/excluded without close/latest-price fallback.
- Intraday replay requires explicit decision eligibility, a valid non-fallback provider state, volume, and finite ordered bid/ask for both the signal and fill; buys use ask and sells use bid.
- Observation, optimized, and historical allocations use `portfolio_risk_budget_v1`: PIT broad-equity market state, proportional capped redistribution, explicit cash, individual/satellite/theme/correlation-cluster caps, and already-loaded adjusted-return histories. Missing correlation or risk history returns deterministic `cash_wait`.
- Daily, intraday, and manual orchestration reuse one prepared legacy evaluation and run V2 lifecycle in an internal `SHADOW` policy. It is ETF-only, serial, capped at 100 positions, idempotent by immutable market evidence, records legacy comparison evidence, and cannot create production actions or notifications.

## Verification

All commands were hard-limited to 55 seconds and run without network proxies.

- Quote/alert and tracked-position tests: 51 passed.
- Daily/intraday execution backtest and evidence tests: 31 passed.
- New lifecycle-shadow and alert-workflow tests: 33 passed.
- Portfolio-risk and optimized-allocation tests: 14 passed.
- Scheduler tests: 5 passed.
- Full intraday ETF watch tests: 66 passed.
- Action-replay and portfolio API tests: 23 passed.
- Existing lifecycle/rollout/action-transition tests: 74 passed.
- Backend domain-boundary tests: 12 passed.
- Ruff passed for all changed application and test files.

## Rollback

- Set `TRACKED_POSITION_LIFECYCLE_SHADOW_ENABLED=false` to stop new shadow evidence immediately; legacy alerts and email behavior remain authoritative either way.
- Roll back the application revision to restore allocation contract v1. There is no schema migration or destructive data rewrite, so accumulated shadow/audit evidence may remain in place.
- Alert and backtest evidence is additive JSON. Rolling back code does not require deleting it and must not relabel prior simulated or SMTP provenance as execution.

## Stable unavailable states

- Missing/stale/fallback quote, missing explicit eligibility, provider divergence, missing bid/ask, crossed book, or non-finite price: web/audit only; no ETF action email or simulated fill.
- Missing PIT broad-equity state, fewer than 60 common risk observations, or fewer than 40 observations for any required correlation pair: allocation is `cash_wait` with explicit reason.
- Missing position episode, exposure version/baseline/quantity, stale state version, predecessor mismatch, or out-of-order market snapshot: shadow evaluation is deferred with a stable reason; legacy evaluation continues.
- Suspension, zero volume, missing adjusted open/factor, or unprovable one-price limit session: daily order remains pending or is excluded without a price fallback.

## Remaining cutover condition

V2 remains shadow-only. Production cutover is outside this change and still requires at least three distinct eligible trading sessions that satisfy the existing rollout gates, followed by explicit review. No automatic execution path exists.

## Scope confirmation

No API schema, database schema, ETF comprehensive-ranking factor/weight, publication threshold, or leader-tactics behavior was changed. The only production behavior changes are stricter ETF email eligibility and the versioned observation-allocation risk budget; both fail closed.
