## 1. Contract and tests

- [x] 1.1 Add pure tests for robust risk-unit differentiation, insufficient adjusted history, persistent high-water, armed state and monotonic trailing protection.
- [x] 1.2 Add integration tests proving raw/ineligible ETF rows cannot drive profit protection and persisted state survives a later lower observation.

## 2. Backend implementation

- [x] 2.1 Add one bounded market-data read for recent decision-eligible total-return-adjusted ETF bars.
- [x] 2.2 Publish `dynamic_etf_threshold_v2` with robust asset-bucket thresholds and frozen entry risk context.
- [x] 2.3 Implement the pure profit-protection transition in risk alerts and consume persisted state in tracked-position analysis.
- [x] 2.4 Persist the armed/high-water/protection state idempotently and generate a truthful per-date chart trajectory.

## 3. API and UI

- [x] 3.1 Add backward-compatible fields for risk-data eligibility, protection state, high-water and actual protection line.
- [x] 3.2 Update the tracked-position UI to distinguish start, giveback and actual protection, with unavailable reasons and no retrospective flat line.

## 4. Verification

- [x] 4.1 Run focused backend and frontend tests in commands hard-limited to 60 seconds.
- [x] 4.2 Run Ruff on touched Python files and the backend domain-boundary test, each hard-limited to 60 seconds.
- [x] 4.3 Run strict OpenSpec validation when CLI is available; otherwise run repository-equivalent artifact checks and review the final diff.
