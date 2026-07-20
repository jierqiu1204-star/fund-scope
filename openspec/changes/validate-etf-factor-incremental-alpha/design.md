## Context

The first ordered change separates `daily_reconstructable_v1` research ranking from `actionable_rank_v1`. This second change uses the resulting point-in-time daily research panel to answer a narrower question: does a candidate factor add stable out-of-sample information and net return beyond the frozen baseline?

Existing label validation aggregates observation-label outcomes, while factor selection needs cross-sectional diagnostics, common-support comparisons, turnover-aware portfolio tests, and a locked holdout. Without a pre-registered experiment, testing many windows and keeping only the best result would overfit the same limited ETF history.

The workflow belongs to Strategy Lab and may read market data and research contracts. It must not write live ranking snapshots, allocation, positions, alerts, notification settings, or production thresholds.

## Goals / Non-Goals

**Goals:**

- Produce reproducible point-in-time factor evidence with a canonical manifest and hash.
- Compare candidates against the frozen baseline on identical dates and eligible ETFs.
- Separate factor predictiveness, portfolio usefulness, implementation cost, stability, and coverage.
- Evaluate sector trend after controlling for technical momentum rather than rewarding duplicated exposure.
- Evaluate longer-history 120/250-session candidates without assuming they improve the current 61-session formula.
- Reserve one untouched chronological holdout and control multiple-candidate selection.

**Non-Goals:**

- Creating `final_score_v4`, selecting production weights, or changing any live consumer.
- Optimizing email thresholds, exit rules, or portfolio sizing.
- Using raw prices, reconstructed future-adjusted vintages without provenance, simulated quotes, or current universe membership for past dates.
- Searching an open-ended grid of factor windows, transforms, caps, and weights.
- Declaring success from in-sample return alone.

## Decisions

### 1. Require an immutable pre-registered manifest

Before outcome calculation, the experiment persists:

- baseline and no more than three candidate identities;
- exact formulas, directions, transforms, missing-value rules, peer buckets, and minimum peer counts;
- point-in-time universe and adjusted-data provenance;
- rebalance rule, Top N, forward horizons, T+1 execution convention, fee and slippage policy;
- chronological split dates, purge and embargo, bootstrap block rule, regimes, primary endpoint, secondary metrics, and promotion tolerances.

The canonical manifest hash is attached to every sample, aggregate, and report. Any material edit creates a new experiment identity; the previous results remain immutable.

Alternative considered: persist parameters after a run. Rejected because outcome-aware parameter recording does not prevent researcher degrees of freedom.

### 2. Freeze one primary estimand

The primary estimand is the paired difference in five-session net return between a candidate Top 10 equal-weight portfolio and the frozen baseline Top 10 on identical rebalance dates and common-support ETFs. Entry is the next decision-eligible adjusted close after the signal date; exit is the adjusted close five sessions after entry. Costs are non-zero, versioned, and charged from realized turnover.

Top 5/20, 1/3/10-session outcomes, IC, risk, churn, and coverage are secondary diagnostics. They cannot replace the primary endpoint after outcomes are seen.

Alternative considered: choose the best horizon and Top N from a grid. Rejected because it optimizes reporting noise and conflicts with the user's realistic five-session focus.

### 3. Build a point-in-time common-support panel

For every historical signal date the panel uses only:

- ETF membership and metadata effective on that date;
- decision-eligible total-return-adjusted OHLCV vintages and provenance available by the cutoff;
- factor values whose full window ends on or before the signal date;
- future adjusted prices only in the outcome stage.

Candidate versus baseline comparisons use the intersection of eligible samples. Separate coverage reports show all-sample availability so that a factor cannot appear better merely by excluding difficult ETFs or dates.

### 4. Separate factor diagnostics from portfolio diagnostics

Per date and peer bucket, the engine calculates cross-sectional Spearman rank IC against 1/3/5/10-session outcomes, IC mean, dispersion, information ratio, sign consistency, and peer count.

It also calculates quantile returns and top-minus-bottom spread, Top 5/10/20 gross and net returns, turnover, rank churn, maximum drawdown, concentration, exclusion rates, and baseline correlation.

Sector trend is residualized against technical momentum within the declared peer universe before its incremental IC and candidate contribution are assessed. Longer-history candidates are evaluated on common support and stratified by the 120-249 and 250-plus history tiers.

### 5. Use purged chronological validation and a locked holdout

Trading dates are split chronologically into development, validation, and final holdout partitions declared before outcomes. Development and validation use expanding walk-forward folds. Each boundary purges overlapping forward labels and applies a 10-session embargo.

The final holdout is calculated once only after the manifest, code version, candidates, and non-holdout results are frozen. Rerunning or changing the candidate family invalidates the prior holdout decision and requires a new future holdout, not another search on the same dates.

### 6. Report uncertainty and multiplicity

The engine uses date-block bootstrap resampling sized for overlapping horizons and reports confidence intervals for the primary paired difference. At most three candidate primary comparisons share one declared multiplicity correction. Regime and history-tier results are stability diagnostics, not additional opportunities to cherry-pick a winner.

A candidate can be labeled `eligible_for_v4_proposal` only when:

- the multiplicity-adjusted primary holdout interval clears the frozen positive threshold;
- the sign is stable across the required walk-forward folds and declared regimes;
- coverage meets the manifest minimum;
- turnover, drawdown, concentration, and exclusion deterioration stay within frozen tolerances.

This label authorizes only a separate small proposal; it never mutates production.

### 7. Keep execution bounded

The experiment processes one date/bucket batch at a time with one worker, at most 20 ETFs per history-fetch batch, checkpointed cursors, and cacheable factor rows. Every command and data operation has a hard timeout of at most 55 seconds. Page reads consume stored reports.

## Risks / Trade-offs

- [Available point-in-time adjusted history may be too short] → Publish `insufficient_data` with exact coverage and do not replace it with current-vintage or simulated evidence.
- [Common support reduces sample size] → Report common-support and all-available coverage side by side; inference uses common support.
- [A factor may look good only in one regime] → Require pre-declared regime stability and show per-regime intervals without selecting regimes after the fact.
- [Fixed costs may miss real market impact] → Freeze a base and conservative sensitivity policy before outcomes; do not infer historical spread from unavailable data.
- [Locked holdout may be consumed by a code defect] → Validate deterministic fixtures and manifest hashes before authorizing the one holdout calculation.
- [Longer windows exclude newer ETFs] → Stratify history tiers and compare on common support rather than rewarding exclusion.

## Migration Plan

1. Require completion of the separate ranking-surface contract and verify point-in-time daily research rows.
2. Add experiment manifest, sample, aggregate, and exclusion records behind a Strategy Lab-only entry point.
3. Validate formulas and no-lookahead behavior on small deterministic fixtures.
4. Run development and walk-forward validation in bounded resumable batches.
5. Freeze code, candidates, thresholds, and evidence hash.
6. Run the final holdout once and publish the immutable research report.
7. If a candidate passes every gate, create a separate `final_score_v4` proposal; otherwise retain current production behavior.

## Open Questions

- Exact chronological split dates depend on verified point-in-time coverage and must be frozen before the first outcome run.
- Fee, slippage, bootstrap block length, and promotion tolerances must use existing project policies where available or be declared in the manifest before outcomes.
