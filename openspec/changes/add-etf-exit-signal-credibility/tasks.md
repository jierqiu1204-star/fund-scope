## 1. Data Model And Schemas

- [x] 1.1 Add Alembic migration for ETF exit signal credibility runs, signal summary items, and optional event sample items.
- [x] 1.2 Add SQLAlchemy models for credibility run metadata, per-signal aggregates, and representative event outcomes.
- [x] 1.3 Add Pydantic schemas for latest report, signal summary, ETF/theme breakdown, and event sample output.
- [x] 1.4 Store contract metadata: signal version, exit rule version, execution model, data cutoff, data window, and contract hash.

## 2. Evaluation Engine

- [x] 2.1 Implement a research-only ETF exit signal credibility service that reads market data and historical signals without importing notifier or tracked-position mutation code.
- [x] 2.2 Generate theoretical historical exit events for `hard_stop`, `trailing_take_profit`, `trend_weakening`, `take_profit_watch`, and `exit_watch`.
- [x] 2.3 Evaluate forward windows for 1, 3, 5, 10, and 20 trading days with no future data used before the trigger point.
- [x] 2.4 Classify outcomes as `success_avoid_loss`, `sold_too_early`, `false_stop`, `neutral`, or `insufficient_future_window`.
- [x] 2.5 Compute per-signal aggregates: sample count, success avoidance rate, false stop rate, sold too early rate, average avoided drawdown, average missed upside, and evidence level.
- [x] 2.6 Compute ETF and theme/bucket breakdowns when metadata and sample counts are sufficient.

## 3. Intraday And Daily Evidence Separation

- [x] 3.1 Implement `intraday_alert` evidence using only `etf_intraday_quotes`; missing intraday data must produce insufficient evidence.
- [x] 3.2 Implement optional `daily_close` evidence as a separate execution model and label it as not proving the live intraday email workflow.
- [x] 3.3 Prevent stale, estimated, display-only, or fallback prices from improving credibility metrics.
- [x] 3.4 Ensure no credibility run writes real alert records, sends emails, or modifies tracked positions.

## 4. API, Admin Job, And Scheduler

- [x] 4.1 Add `POST /api/short-research/etf-exit-credibility/run` for manual research-only generation.
- [x] 4.2 Add `GET /api/short-research/etf-exit-credibility/latest` for the latest report.
- [x] 4.3 Add admin job `etf_exit_signal_credibility` with structured counts, data window, conclusion, and insufficiency reasons.
- [x] 4.4 Add a night scheduler entry after hyperopt, without affecting intraday watch or daily signal jobs.
- [x] 4.5 Ensure job failure writes normal job error details and does not block market data or email tasks.

## 5. Frontend Evidence Display

- [x] 5.1 Add an “退出信号可信度” section to the ETF strategy evidence page.
- [x] 5.2 Display each signal type with sample count, evidence level, success avoidance rate, false stop rate, sold too early rate, average avoided drawdown, and average missed upside.
- [x] 5.3 Show execution model, data window, contract status, and sample insufficiency reasons.
- [x] 5.4 Show `等待验证` or `样本不足` when no current-contract report or insufficient samples exist.
- [x] 5.5 Keep short-term ranking, tracking cards, live alerts, and email UI unchanged.

## 6. Tests

- [x] 6.1 Add backend tests for trailing take profit followed by continued decline and continued rally.
- [x] 6.2 Add backend tests for hard stop followed by continued decline and quick rebound.
- [x] 6.3 Add backend tests proving missing intraday data does not fall back to daily close for `intraday_alert` evidence.
- [x] 6.4 Add backend tests proving credibility runs do not write real alerts, do not send emails, and do not modify tracked positions.
- [x] 6.5 Add API tests for manual run and latest report endpoints.
- [x] 6.6 Add frontend type coverage for the new report response fields.

## 7. Boundary And Regression Verification

- [x] 7.1 Run `uv run pytest tests/test_backend_domain_boundaries.py`.
- [x] 7.2 Run new credibility tests and related ETF evidence tests.
- [x] 7.3 Run `uv run ruff check .`.
- [x] 7.4 Run `corepack pnpm exec tsc --noEmit`.
- [ ] 7.5 Use server Docker static build verification if frontend static build validation is required.

## 8. Deployment And Data Refresh

- [ ] 8.1 Deploy to `110.42.222.9` after tests pass.
- [ ] 8.2 Run Alembic migration on the server.
- [ ] 8.3 Manually run `etf_exit_signal_credibility` once on the server.
- [ ] 8.4 Verify the ETF strategy evidence page shows exit signal credibility without changing live email behavior.
- [ ] 8.5 Confirm no new real email, real alert, or tracked position mutation occurs during the credibility run.
