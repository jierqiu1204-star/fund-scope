## 1. Coverage And Data Audit

- [x] 1.1 Add ETF universe coverage loader that counts all ETFs, eligible ETFs, enough daily-history ETFs, enough intraday-history ETFs, final optimized ETFs, and exclusions.
- [x] 1.2 Change ETF exit Hyperopt default behavior from first-300 sampling to full eligible coverage.
- [x] 1.3 Keep explicit `max_assets` support for manual sampled runs and mark sampled runs as partial coverage.
- [x] 1.4 Persist coverage funnel and exclusion reasons in Hyperopt run summary JSON.
- [x] 1.5 Add server/admin job output fields for coverage counts, sampled flag, and final optimized count.

## 2. Intraday Alert Replay Engine

- [x] 2.1 Make `intraday_alert` the primary execution model for ETF exit Hyperopt.
- [x] 2.2 Load historical fresh, decision-eligible intraday quotes by ETF without using stale or display-only quotes.
- [x] 2.3 Replay theoretical exit signals using the same live exit rule contract and no future data.
- [x] 2.4 Simulate manual execution after configurable delay, default 3 minutes, using the next eligible intraday quote.
- [x] 2.5 Mark missing delayed fills as unfilled and prevent daily-close fallback.
- [x] 2.6 Keep `daily_close` optimization available only as explicitly labeled coarse reference.

## 3. Parameter Search And Baseline Comparison

- [x] 3.1 Expand parameter evaluation to compare each candidate against current live default thresholds.
- [x] 3.2 Add baseline metrics: return, max drawdown, false exits, missed upside, email count, turnover, unfilled alerts, and sample counts.
- [x] 3.3 Update candidate classification to require out-of-sample improvement over baseline.
- [x] 3.4 Add rolling-window stability checks for intraday candidates.
- [x] 3.5 Reject candidates with insufficient samples, excessive false exits, excessive emails, or worse out-of-sample drawdown.
- [x] 3.6 Record candidate rejection reasons in run/item payloads.

## 4. Live Rule Safety Gate

- [x] 4.1 Update approved-parameter lookup to require `execution_model=intraday_alert`.
- [x] 4.2 Require matching contract hash, approved status, sufficient coverage, and non-expired candidate before live use.
- [x] 4.3 Keep current dynamic defaults when no approved intraday candidate matches.
- [x] 4.4 Include threshold source, execution model, coverage status, run id, candidate id, and bucket key in tracked ETF threshold context.
- [x] 4.5 Ensure stale, daily-close, estimated, display-only, or unavailable prices still block actionable email.

## 5. API And Frontend Evidence

- [x] 5.1 Extend ETF exit Hyperopt latest/run API payload with coverage funnel, sampled flag, baseline metrics, candidate-vs-baseline comparison, and execution model.
- [x] 5.2 Update strategy evidence page to separate current live rule, daily-close reference, and intraday optimized candidate.
- [x] 5.3 Show clear states: full coverage, partial coverage, evidence insufficient, rejected, candidate pending approval, approved live rule.
- [x] 5.4 Display why no optimized parameter is active when candidates fail or are research-only.
- [x] 5.5 Avoid any UI wording that implies rejected or unapproved candidates are live email rules.

## 6. Scheduler And Admin

- [x] 6.1 Register or update nightly ETF exit Hyperopt job to run after ETF data, signal generation, and credibility evidence jobs.
- [x] 6.2 Make admin manual run accept optional sample size and execution model, defaulting to full intraday coverage.
- [x] 6.3 Ensure job failure writes `job_runs.error_message` and does not affect intraday watch or tracked-position email jobs.
- [x] 6.4 Add structured job result fields for coverage, baseline comparison, candidate count, rejected count, and evidence-insufficient count.

## 7. Tests

- [x] 7.1 Add tests proving full default coverage does not apply first-300 sampling.
- [x] 7.2 Add tests for sampled runs being marked partial coverage.
- [x] 7.3 Add tests for intraday replay using only past fresh quotes.
- [x] 7.4 Add tests for 3-minute delayed execution using the next available intraday quote.
- [x] 7.5 Add tests proving missing intraday fill does not fall back to daily close.
- [x] 7.6 Add tests comparing candidate parameters against baseline and rejecting worse out-of-sample results.
- [x] 7.7 Add tests for approved-parameter lookup requiring intraday evidence and matching contract hash.
- [x] 7.8 Add tests proving Hyperopt does not send emails, create alerts, or mutate tracked positions.
- [x] 7.9 Update backend domain boundary tests if new modules are added.

## 8. Validation

- [x] 8.1 Run `uv run pytest tests/test_etf_exit_hyperopt.py tests/test_tracked_positions.py tests/test_etf_exit_credibility.py`.
- [x] 8.2 Run `uv run pytest tests/test_backend_domain_boundaries.py tests/test_scheduler.py tests/test_short_research_api.py`.
- [x] 8.3 Run `uv run ruff check .`.
- [x] 8.4 Run `corepack pnpm exec tsc --noEmit`.
- [x] 8.5 Do not rely on local static build if the Windows sandbox hangs; validate static build in server Docker during deployment.

## 9. Server Verification

- [x] 9.1 Deploy migrations and code to the server.
- [ ] 9.2 Manually run ETF exit Hyperopt with default settings and confirm it reports full eligible coverage.
- [ ] 9.3 Verify the optimized count is materially above the old 271 when enough intraday history exists, or that exclusions explain the gap.
- [ ] 9.4 Verify latest evidence API separates current live rule from intraday candidate rule.
- [ ] 9.5 Verify tracked ETF live emails still use current defaults unless an approved intraday candidate exists.
- [ ] 9.6 Verify no real notification logs or tracked-position alerts are created by the Hyperopt job.
