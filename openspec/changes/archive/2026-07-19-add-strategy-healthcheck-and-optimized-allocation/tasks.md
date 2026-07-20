## 1. Evidence Contract And Data Model

- [x] 1.1 Review current ETF evidence contract fields and define healthcheck/optimized-allocation metadata without changing existing hashes.
- [x] 1.2 Add database migration for ETF strategy healthcheck snapshots and healthcheck item breakdowns.
- [x] 1.3 Add database migration for optimized allocation snapshots and allocation method items.
- [x] 1.4 Add SQLAlchemy models and schemas for healthcheck snapshots, breakdown items, optimized allocation snapshots, and method items.
- [x] 1.5 Add repository/query helpers that read only market data and short research signals; do not import tracking or notifier modules.

## 2. ETF Strategy Healthcheck Backend

- [x] 2.1 Implement healthcheck service that builds full-sample, recent-window, and custom-window strategy diagnostics.
- [x] 2.2 Implement label-level diagnosis for observation labels and entry timing labels with sample count, forward returns, win rate, drawdown, and confidence state.
- [x] 2.3 Implement theme and market-regime breakdown for ETF outcomes.
- [x] 2.4 Implement conclusion rules: `可继续观察`, `待验证`, `近期失效`, `数据不足`.
- [x] 2.5 Separate daily-close evidence from intraday-alert evidence and prevent daily fallback for missing intraday history.
- [x] 2.6 Persist healthcheck snapshots with evidence contract hash, signal run id, data window, execution model, and generated time.

## 3. Optimized Allocation Backend

- [x] 3.1 Implement eligible ETF return matrix builder using only verified or alternate-provider daily data.
- [x] 3.2 Implement covariance and correlation summary with data sufficiency checks.
- [x] 3.3 Implement equal-weight baseline allocation with existing ETF eligibility and cap constraints.
- [x] 3.4 Implement minimum-volatility style allocation with single ETF 30% cap and theme concentration cap.
- [x] 3.5 Implement risk-parity or HRP-style approximate allocation with the same constraints.
- [x] 3.6 Return unavailable optimization status when inputs are insufficient instead of emitting placeholder weights.
- [x] 3.7 Persist optimized allocation snapshots with method labels, constraints, data window, selected ETFs, weights, and explanation.

## 4. Backtest And Validation Integration

- [x] 4.1 Extend ETF portfolio backtest to compare rule-based, optimized, and equal-weight allocation methods.
- [x] 4.2 Ensure optimized allocation is skipped per-date when the optimizer cannot produce valid weights.
- [x] 4.3 Keep daily-close and intraday-alert execution models separate in all backtest outputs.
- [x] 4.4 Connect ETF signal validation results to healthcheck only when evidence contract hashes match.
- [x] 4.5 Mark old-contract validation or backtest results as old-method evidence.

## 5. API And Admin Jobs

- [x] 5.1 Add API endpoint to list latest ETF strategy healthcheck summary and detail.
- [x] 5.2 Add API endpoint or extend existing observation portfolio response with optimized allocation comparison.
- [x] 5.3 Add admin job for ETF strategy healthcheck generation.
- [x] 5.4 Add admin job for optimized allocation generation.
- [x] 5.5 Ensure admin job results include counts, conclusion, data window, and unavailable reasons.

## 6. Frontend

- [x] 6.1 Add `/short-term` strategy healthcheck panel showing conclusion, recent-window status, and failing labels/themes.
- [x] 6.2 Add ETF funds configuration comparison UI for rule-based, optimized, and equal-weight allocations.
- [x] 6.3 Show optimizer method, data window, constraints, unavailable reason, and research-only disclaimer.
- [x] 6.4 Show daily-close evidence and intraday-alert evidence as separate cards.
- [x] 6.5 Ensure old-contract results are visually marked as old-method evidence.
- [x] 6.6 Keep existing tracking, email, and live ranking UI behavior unchanged.

## 7. Tests

- [x] 7.1 Add backend tests for healthcheck conclusion rules and recent-window degradation.
- [x] 7.2 Add backend tests for label-level diagnosis with enough samples and sparse samples.
- [x] 7.3 Add backend tests for optimizer constraints: single ETF cap, theme cap, unavailable input, and no placeholder weights.
- [x] 7.4 Add backtest tests for rule-based vs optimized vs equal-weight comparison.
- [x] 7.5 Add evidence contract tests for old-method validation and backtest results.
- [x] 7.6 Add frontend TypeScript coverage for new response fields.

## 8. Boundary And Regression Verification

- [x] 8.1 Run `uv run pytest tests/test_backend_domain_boundaries.py`.
- [x] 8.2 Run ETF-specific backend tests for short research, signal validation, portfolio backtest, and tracked positions.
- [x] 8.3 Run `uv run ruff check .`.
- [x] 8.4 Run `corepack pnpm exec tsc --noEmit`.
- [x] 8.5 Run `corepack pnpm build:static` with timeout discipline; stop and diagnose if it produces no output for several minutes.
- [ ] 8.6 Capture `/short-term` PC and mobile screenshots and confirm no regression in existing tracking and live ranking layout.

## 9. Deployment

- [x] 9.1 Run database migration locally or in test container.
- [ ] 9.2 Deploy to `110.42.222.9`.
- [ ] 9.3 Run Alembic migration on server.
- [ ] 9.4 Manually trigger ETF signal, healthcheck, optimized allocation, and backtest jobs on server.
- [ ] 9.5 Verify `/short-term` shows strategy healthcheck and optimized allocation comparison without changing tracking/email behavior.
