## 1. Verify Prerequisite Contracts

- [x] 1.1 Verify `separate-etf-research-and-actionable-ranks` is implemented and stable before reading factor outcomes.
- [x] 1.2 Add dependency tests proving Strategy Lab may read market data and research contracts but cannot mutate production ranking, allocation, positions, alerts, or notifications.
- [x] 1.3 Freeze deterministic baseline fixtures for `daily_reconstructable_v1` and the ranking-surface contract hashes.

## 2. Register Immutable Experiments

- [x] 2.1 Write tests for canonical manifests, at-most-three candidates, complete pre-registration fields, deterministic hashes, and rejection before outcome reads.
- [x] 2.2 Implement immutable experiment, candidate, split, execution-cost, promotion-gate, and holdout-consumption records.
- [x] 2.3 Add tests proving any material manifest edit creates a new experiment identity and cannot reuse prior holdout evidence.

## 3. Build The Point-In-Time Panel

- [x] 3.1 Write no-lookahead tests for historical universe membership, source cutoff, adjusted-data provenance, factor windows, and future-outcome isolation.
- [x] 3.2 Implement common-support panel construction from decision-eligible total-return-adjusted daily data with explicit exclusion reasons.
- [x] 3.3 Implement T+1 adjusted-close entry, declared 1/3/5/10-session exits, non-zero turnover costs, and pending-window handling.
- [x] 3.4 Add coverage reports that separate common-support samples from baseline and candidate all-available samples.

## 4. Implement Factor And Portfolio Diagnostics

- [x] 4.1 Add deterministic tests and implementation for cross-sectional Spearman IC, IC dispersion, information ratio, sign consistency, peer counts, quantile returns, and top-minus-bottom spreads.
- [x] 4.2 Add deterministic tests and implementation for Top 5/10/20 gross and net returns, turnover, rank churn, drawdown, concentration, and exclusion rates.
- [x] 4.3 Add correlation and marginal-contribution diagnostics so redundant factors are not counted as independent evidence.
- [x] 4.4 Add sector-trend residualization against technical momentum within the pre-declared peer universe.
- [x] 4.5 Add common-support and history-tier diagnostics for pre-registered 120/250-session factor candidates.

## 5. Enforce Out-Of-Sample Validation

- [x] 5.1 Implement chronological development, validation, and holdout roles with expanding walk-forward folds, overlapping-label purge, and a 10-session embargo.
- [x] 5.2 Add date-block bootstrap intervals and the pre-declared multiplicity adjustment across at most three primary comparisons.
- [x] 5.3 Add promotion-state tests for adjusted primary interval, fold and regime stability, coverage, turnover, drawdown, concentration, and exclusion gates.
- [x] 5.4 Prove secondary Top N or horizon results cannot replace the paired Top 10 five-session net-excess primary endpoint.
- [x] 5.5 Freeze the manifest, code version, candidate set, thresholds, and non-holdout evidence before enabling the final holdout.
- [x] 5.6 Run the final holdout once, record consumption, and reject outcome-driven retries under the same experiment identity.

## 6. Persist And Display Research Evidence

- [x] 6.1 Persist samples, aggregates, exclusions, intervals, split roles, and reports under the experiment and ranking-contract hashes.
- [x] 6.2 Add a Strategy Lab evidence endpoint or existing research view that separates development, validation, and holdout results and shows costs and limitations.
- [x] 6.3 Add regression tests proving strong or weak factor evidence cannot change live scores, weights, labels, portfolio output, tracked positions, alerts, or email rules.
- [x] 6.4 Permit only a separate `final_score_v4` proposal when every frozen promotion gate passes; otherwise record that the current ranking is retained.

## 7. Bound And Verify Execution

- [x] 7.1 Process at most 20 ETFs per history-fetch batch with one worker, cached factor rows, checkpointed date cursors, and idempotent resume.
- [x] 7.2 Enforce hard command and data-operation timeouts of at most 55 seconds and prevent concurrent duplicate experiments.
- [x] 7.3 Run deterministic, no-lookahead, evidence-contract, domain-boundary, and strict OpenSpec validation with individually bounded commands.
- [x] 7.4 Record production-shaped runtime, coverage, exclusion, checkpoint, and memory evidence without starting an unbounded full-history synchronization.

## 8. Repair Acceptance Regressions

- [x] 8.1 Require a verified exchange-session calendar for T+1 entry and declared exits so missing outcome rows remain pending instead of shifting the execution date.
- [x] 8.2 Wire Holm-Bonferroni adjustment into persisted factor evidence and reject evidence whose claimed adjusted values do not match its raw primary p-values.
- [x] 8.3 Make database lease acquisition atomic across workers while retaining bounded, resumable execution.
- [x] 8.4 Re-run bounded factor, evidence, domain-boundary, Ruff, and strict OpenSpec validation.
