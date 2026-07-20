## 1. Data Model And Contracts

- [x] 1.1 Add Alembic migration for ETF exit calibration runs table with rule versions, data window, cutoff time, bucket summary, status, and error message.
- [x] 1.2 Add Alembic migration for ETF exit calibration candidates table with bucket key, parameters, train/oos/rolling metrics, confidence state, score, status, and source reliability.
- [x] 1.3 Add SQLAlchemy models and Pydantic schemas for calibration run, candidate, latest evidence response, and manual job result.
- [x] 1.4 Add constants for calibration rule version, parameter search bounds, minimum sample counts, and candidate status values.
- [x] 1.5 Extend ETF research evidence contract helpers to include calibration rule version, candidate id, approved parameter id, and evidence status.

## 2. Calibration Engine

- [x] 2.1 Create `app.services.etf_exit_calibration` module without importing notifier, tracked position mutation paths, or frontend-facing API modules.
- [x] 2.2 Implement ETF bucket assignment using ETF type, industry/theme tags, volatility bucket, and fallback `unknown` bucket.
- [x] 2.3 Implement parameter search generation for hard stop, take-profit watch, trailing start, trailing giveback, and trend confirmation.
- [x] 2.4 Implement no-lookahead replay using decision-eligible ETF intraday quotes and daily history only when the execution model explicitly allows daily data.
- [x] 2.5 Implement train, out-of-sample, and rolling-window split logic.
- [x] 2.6 Implement robustness objective with drawdown, false exits, missed upside, email count, turnover, participation, and sample sufficiency penalties.
- [x] 2.7 Implement confidence shrinkage for low-sample success and false-exit rates.
- [x] 2.8 Persist calibration runs and candidates without creating tracked-position alerts, notification logs, or position mutations.

## 3. Risk Alert Integration

- [x] 3.1 Add read-only approved-parameter lookup used by `risk_alerts` without importing the calibration engine.
- [x] 3.2 Update ETF exit threshold calculation to use matching approved calibrated parameters when available.
- [x] 3.3 Keep existing dynamic default thresholds when no approved current parameter exists.
- [x] 3.4 Include threshold source, calibration run id, candidate id, bucket key, and calibration version in tracked ETF exit output.
- [x] 3.5 Ensure stale, display-only, estimated, or unavailable data still blocks actionable email even when approved parameters exist.

## 4. Scheduler And Admin Jobs

- [x] 4.1 Add `etf_exit_calibration` admin job and scheduler registration after daily ETF data, signal, credibility, and hyperopt prerequisites.
- [x] 4.2 Add manual admin run endpoint that executes calibration and returns structured candidate summary.
- [x] 4.3 Ensure job failure writes `job_runs.error_message` and does not affect intraday watch or daily tracked-position alerts.
- [x] 4.4 Add job result fields for bucket count, candidate count, rejected count, evidence-insufficient count, and best candidate summary.

## 5. API And Frontend Evidence Page

- [x] 5.1 Add read-only API for latest ETF exit calibration evidence.
- [x] 5.2 Add API response fields for current active exit parameters, candidate parameters, train/oos/rolling metrics, confidence state, and evidence freshness.
- [x] 5.3 Update ETF strategy evidence page to show current live rule and candidate optimized rule separately.
- [x] 5.4 Show why a candidate is not automatically active, including sample insufficiency, out-of-sample degradation, rejected status, or pending approval.
- [x] 5.5 Ensure UI does not imply candidate parameters are live email rules unless status is approved and contract matches.

## 6. Tests

- [x] 6.1 Add unit tests for parameter search bounds and deterministic candidate generation.
- [x] 6.2 Add unit tests for no-lookahead replay and missing intraday data exclusion.
- [x] 6.3 Add unit tests for sample split, out-of-sample rejection, rolling instability, and evidence-insufficient status.
- [x] 6.4 Add unit tests for confidence shrinkage on low-sample high-success candidates.
- [x] 6.5 Add unit tests proving calibration does not create alerts, send emails, or mutate tracked positions.
- [x] 6.6 Add integration tests for approved-parameter lookup and fallback to default dynamic rules.
- [x] 6.7 Add API tests for manual run and latest evidence endpoints.
- [x] 6.8 Update backend domain boundary tests to keep calibration in the research/evidence layer and keep notifier isolated.

## 7. Validation

- [x] 7.1 Run `uv run pytest tests/test_etf_exit_hyperopt.py tests/test_tracked_positions.py tests/test_etf_exit_credibility.py`.
- [x] 7.2 Run `uv run pytest tests/test_backend_domain_boundaries.py tests/test_scheduler.py tests/test_short_research_api.py`.
- [x] 7.3 Run `uv run ruff check .`.
- [x] 7.4 Run `corepack pnpm exec tsc --noEmit`.
- [x] 7.5 Skip local static build unless needed; validate static build in server Docker if deploying.

## 8. Server Verification

- [ ] 8.1 Apply database migration on the server.
- [ ] 8.2 Manually run `etf_exit_calibration` once after latest ETF data and credibility evidence are available.
- [ ] 8.3 Verify latest evidence API returns candidates and marks them research-only by default.
- [ ] 8.4 Verify current tracked ETF email rules remain unchanged unless an approved parameter exists.
- [ ] 8.5 Verify strategy evidence page separates current rule from candidate optimized rule.
