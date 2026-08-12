## 1. Fail-closed ETF alert eligibility

- [x] 1.1 Add tests proving daily and intraday ETF action emails require a fresh explicitly decision-eligible intraday snapshot, while confirmed fund NAV behavior remains unchanged.
- [x] 1.2 Change legacy quote eligibility so a missing `decision_eligible` field is false and stale/fallback/display-only ETF evidence remains web-only.
- [x] 1.3 Apply the fresh ETF quote gate to daily as well as intraday action-email decisions without changing webpage-only analysis.
- [x] 1.4 Add bounded bid/ask execution-risk evidence to alert and audit context, keeping reminders non-executing and missing executable prices unavailable.

## 2. Honest backtest execution

- [x] 2.1 Add regression tests that reject same-day close fills and require next-eligible-session adjusted-open fills with observed gap and frozen costs.
- [x] 2.2 Reuse the existing action-replay fill selector, cost constants, and lot rounding in the ETF portfolio backtest.
- [x] 2.3 Keep suspended, zero-volume, limit-blocked, missing-price, and ineligible intraday orders pending or excluded without close/latest-price fallback.
- [x] 2.4 Report base and stressed execution costs separately and preserve `simulated_not_observed` provenance.

## 3. Shared portfolio risk budget

- [x] 3.1 Add tests exposing the sequential cap bug and requiring proportional constrained weights, explicit cash, theme/cluster caps, and deterministic unavailable behavior.
- [x] 3.2 Replace sequential cap filling with a shared proportional capped-redistribution helper that leaves infeasible residual as cash.
- [x] 3.3 Derive a pre-allocation PIT market-risk state from eligible broad-equity evidence and enforce mode-specific primary/satellite exposure and cash limits.
- [x] 3.4 Wire the existing per-asset volatility/drawdown reducer and correlation-cluster cap into production allocation using already-loaded histories.
- [x] 3.5 Reuse the same risk contract in optimized allocation and historical backtest, and record portfolio volatility, drawdown, common sample, theme/cluster exposure, constraints, version, and hash in existing evidence JSON.

## 4. V2 lifecycle shadow wiring

- [x] 4.1 Add tests for shadow-disabled, shadow-enabled, ETF-only, no-production-side-effect, shared-analysis, idempotent rerun, ineligible-data freeze, out-of-order skip, and per-position failure isolation behavior.
- [x] 4.2 Extract one prepared legacy position evaluation and add a pure adapter that emits the complete V2 rule set, including false/recovery rules, without a second history/provider query.
- [x] 4.3 Add a workflow-level best-effort shadow observer with an internal `SHADOW` policy, immutable market-derived snapshot identity, legacy comparison evidence, a 100-position bound, and stable aggregate counters.
- [x] 4.4 Wire daily scheduler, intraday workflow, and manual admin orchestration to the same wrapper while keeping legacy alert/email output authoritative.
- [x] 4.5 Add a default-on shadow-only settings flag and verify disabling it leaves legacy behavior unchanged.

## 5. Verification and handoff

- [x] 5.1 Run focused quote/alert, backtest, portfolio allocation, optimized allocation, and V2 lifecycle test groups with each command hard-limited to 60 seconds.
- [x] 5.2 Run `tests/test_backend_domain_boundaries.py` and split Ruff checks into bounded groups; fix all failures related to this change.
- [x] 5.3 Run strict OpenSpec validation and confirm no API/schema change, no ranking-weight change, no leader-tactics coupling, and no automatic execution path.
- [x] 5.4 Record implementation evidence, rollback switches, known unavailable states, and remaining three-session shadow cutover requirement.
