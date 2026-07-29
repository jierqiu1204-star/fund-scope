## 1. Freeze Prerequisites And Contracts

- [x] 1.1 Verify the dual research/actionable ranking contracts, factor experiment contracts, ranking candidates, and action lifecycle versions remain unchanged.
- [x] 1.2 Add contract tests for the 252-session, 40-independent-date, three-fold, dual-95-percent, adjusted-interval, and two-percentage-point drawdown promotion gates.
- [x] 1.3 Add tests proving only the three frozen candidates are accepted and exploratory Top N or horizon results cannot replace the primary endpoint.

## 2. Build The Operational Research Coordinator

- [x] 2.1 Add an immutable research-loop manifest and deterministic hash that binds ranking, candidate, cutoff, input, feature, execution, cost, validation, code, and holdout identities.
- [x] 2.2 Add a resumable phase/checkpoint contract with atomic single-worker lease, idempotent page identity, and manifest-mismatch rejection.
- [x] 2.3 Implement one bounded continuation that orchestrates replay inputs, Stage A, Stage B, frozen candidates, forward outcomes, validation, and evidence persistence without duplicating domain algorithms.
- [x] 2.4 Implement adaptive page sizing between 5 and 20 ETFs, starting at 10, with timeout/memory backoff and healthy-page growth.
- [x] 2.5 Prove interruption points and safe page-size changes produce identical final artifacts, ranks, outcomes, and hashes.

## 3. Enforce Point-In-Time Inputs

- [x] 3.1 Wire authoritative membership facts and adjusted-row availability cutoffs into the coordinator and reject later-backfilled historical inputs.
- [x] 3.2 Preserve separate universe, adjusted-price, feature, score, and forward-outcome coverage dimensions with stable exclusions.
- [x] 3.3 Add regression tests proving raw Sina/efinance, stale, estimated, current-survivor, and current-ranking fallbacks cannot increase research eligibility.
- [x] 3.4 Add production-shaped insufficient-data evidence for the current short PIT history without fabricating historical visibility.

## 4. Operationalize Ranking And Factor Evidence

- [x] 4.1 Connect existing factor IC, quantile, spread, residual, redundancy, and marginal-contribution diagnostics to persisted experiment evidence.
- [x] 4.2 Connect Top10 five-session paired net excess, non-overlapping dates, turnover costs, churn, drawdown, concentration, coverage, and block-bootstrap evidence to the coordinator.
- [x] 4.3 Enforce expanding walk-forward folds, 10-session purge/embargo, at-most-three Holm comparisons, and one-time holdout consumption in the operational path.
- [x] 4.4 Persist explicit `insufficient_data`, `promotion_ineligible`, and `promotion_eligible` states without changing `final_score_v3`.
- [x] 4.5 Add tests proving theme catalyst, flow, sentiment, valuation, or other shadow factors remain non-score-bearing until a separate promotion proposal.

## 5. Connect Research-Only Policy Shadow

- [x] 5.1 Adapt complete point-in-time Top20 ranking cohorts into the existing pure portfolio and `etf_exit_action_v3` replay lifecycle.
- [x] 5.2 Persist Top20 ten-session cost-adjusted action-cycle benefit relative to holding and separate stop/profit directional diagnostics.
- [x] 5.3 Preserve simulated, SMTP-accepted, provider-delivered, and user-confirmed execution provenance as independent result groups and sample gates.
- [x] 5.4 Add database snapshot and dependency tests proving policy shadow cannot mutate production rankings, allocations, positions, alerts, notifications, or SMTP state.

## 6. Expose Truthful Evidence APIs

- [x] 6.1 Extend the existing research evidence response with ranking source, policy mode, cutoff, manifest, coverage, exclusions, endpoint labels, samples, costs, intervals, holdout, notification, execution, and unavailable-reason fields.
- [x] 6.2 Add stable unavailable reasons for missing production publication, replay, compatibility, PIT universe, adjusted prices, score coverage, independent dates, future windows, entry/exit prices, live notifications, and confirmed executions.
- [x] 6.3 Add API tests proving production, replay, policy shadow, live notification, and confirmed execution evidence cannot be merged or upgraded implicitly.
- [x] 6.4 Keep legacy clients compatible and label evidence without the current manifest as legacy or incompatible.

## 7. Separate Evidence In The Workbench

- [x] 7.1 Extend frontend evidence types for the additive API contract and primary/exploratory endpoint labels.
- [x] 7.2 Render production ranking, historical research replay, policy shadow, live notification, provider delivery, and confirmed execution as separate sections.
- [x] 7.3 Show exact `样本不足`, `覆盖不足`, `等待未来窗口`, `版本不一致`, and unavailable counts without presenting fallback metrics.
- [x] 7.4 Add frontend tests proving simulated evidence cannot be presented as live delivery, execution, or production strategy performance.

## 8. Bound, Validate, And Record Real Evidence

- [x] 8.1 Enforce at-most-55-second command/data-operation timeouts, one continuation per trigger, one worker, and no unbounded loop or concurrent experiment.
- [x] 8.2 Run deterministic coordinator, PIT, ranking, factor, policy-shadow, evidence API, frontend, and no-side-effect tests in separately bounded commands.
- [x] 8.3 Run backend domain-boundary and Ruff checks plus strict OpenSpec validation in separately bounded commands.
- [ ] 8.4 Verify `accelerate-etf-publish-readiness-sync` real production tasks 8.4–8.6 and record authoritative universe, dual coverage, provider health, resource profile, publication, skip, and rollback evidence.
- [ ] 8.5 Run bounded prospective research continuations and record factual eligible sessions, independent primary dates, folds, exclusions, peak RSS, and resume evidence.
- [x] 8.6 Confirm the current result remains `insufficient_data` until all real-data gates pass and record that no production score, allocation, alert, or notification policy changed.
