## 1. Baseline And Boundaries

- [x] 1.1 Confirm current dirty worktree and list unrelated modified files before implementation
- [x] 1.2 Identify existing tables, services, and tests for short research, ETF quotes, observation portfolio, tracked positions, and reliability
- [x] 1.3 Define shared constants for quote reliability, validation confidence, and research-quality labels without changing existing enum values

## 2. Data Model And Persistence

- [x] 2.1 Add persistence for ETF label validation runs and validation items
- [x] 2.2 Add or extend persistence for optimized observation portfolio snapshots and per-asset weight explanations
- [x] 2.3 Ensure tracked-position alert audits store threshold rule version, threshold inputs, quote reliability, and no-email reason
- [x] 2.4 Add migrations with backward-compatible defaults and no destructive data changes

## 3. Label Validation

- [x] 3.1 Implement historical ETF signal replay using only data available at each signal timestamp
- [x] 3.2 Calculate 1/3/5/10 trading-day forward return, max drawdown, win rate, sample count, and exclusions for label combinations
- [x] 3.3 Add recent-degradation comparison with sample-size gating
- [x] 3.4 Add API endpoint for latest ETF label validation summaries
- [x] 3.5 Add job runner integration so validation can run after ETF signal generation

## 4. Observation Portfolio Optimization

- [x] 4.1 Build deterministic ETF candidate preparation using decision-eligible data only
- [x] 4.2 Implement constrained weight generation with single ETF cap 30% and theme cap 45%
- [x] 4.3 Implement simplified risk-based fallback when optimization inputs are insufficient
- [x] 4.4 Return per-ETF weight reasons and exclusion reasons in portfolio API responses

## 5. Adaptive Exit And Alert Explanation

- [x] 5.1 Expose ETF exit threshold rule version, volatility unit, dynamic threshold values, and distance-to-trigger in tracked-position responses
- [x] 5.2 Persist replayable threshold context for sent, skipped, and no-alert evaluations
- [x] 5.3 Ensure stale, diverged, estimated, unavailable, or display-only prices cannot trigger email alerts
- [x] 5.4 Add no-email reason output for threshold-not-crossed, data-ineligible, market-closed, and cooldown states

## 6. Data Reliability Integration

- [x] 6.1 Normalize quote reliability values across live rankings, tracked positions, portfolio optimization, validation, and alert audits
- [x] 6.2 Ensure daily reference labels cannot overwrite fresh intraday context on ETF realtime views
- [x] 6.3 Make optimized weights and validation calculations reject display-only or stale data

## 7. Frontend Workbench

- [x] 7.1 Add label validation evidence and confidence display to `/short-term`
- [x] 7.2 Add optimized observation weight or exclusion reason to ETF detail
- [x] 7.3 Add data reliability and no-alert reason display to tracked holding cards/details
- [x] 7.4 Keep all new wording observation-only and avoid buy/sell command language

## 8. Tests And Verification

- [x] 8.1 Add backend tests for no-lookahead label validation and sample gating
- [x] 8.2 Add backend tests for optimized weights, caps, fallback, and exclusion reasons
- [x] 8.3 Add backend tests for adaptive exit context and data-ineligible email blocking
- [x] 8.4 Add frontend type checks for new response fields and UI states
- [x] 8.5 Run `uv run pytest`, `uv run ruff check .`, `corepack pnpm exec tsc --noEmit`, and `corepack pnpm build:static`

## 9. Deployment Readiness

- [x] 9.1 Verify migrations run on a copy or safe target without losing existing tracking data
- [x] 9.2 Verify `/short-term` loads from cached results and does not recompute all ETF history on page load
- [ ] 9.3 Deploy to server and confirm health, short-term page, latest validation evidence, portfolio weights, and tracked-position no-alert explanations



