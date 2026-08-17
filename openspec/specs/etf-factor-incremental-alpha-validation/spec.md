# etf-factor-incremental-alpha-validation Specification

## Purpose
TBD - created by archiving change validate-etf-factor-incremental-alpha. Update Purpose after archive.
## Requirements

### Requirement: Factor Experiments Are Pre-Registered And Immutable
The system SHALL persist a canonical factor experiment manifest and hash before calculating outcomes.

#### Scenario: Experiment is registered
- **WHEN** a user creates a factor validation experiment
- **THEN** the manifest records the frozen baseline, no more than three candidates, formulas, directions, transforms, universe, peer buckets, horizons, costs, execution rule, splits, exclusions, primary endpoint, uncertainty method, and promotion tolerances

#### Scenario: Material parameter changes
- **WHEN** a frozen formula, candidate, split, cost, endpoint, or tolerance changes
- **THEN** the system creates a new experiment identity and MUST NOT overwrite or merge the prior evidence

#### Scenario: Outcome run lacks a frozen manifest
- **WHEN** an outcome calculation is requested without a complete pre-registered manifest
- **THEN** the system rejects the run before reading future returns

### Requirement: Factor Samples Are Point-In-Time And Decision-Eligible
The system SHALL build historical factor samples only from ETF membership, metadata, total-return-adjusted OHLCV provenance, and factor inputs available on or before each signal cutoff.

#### Scenario: Historical factor is calculated
- **WHEN** a factor sample is reconstructed for signal date T
- **THEN** every factor input is dated on or before T and the experiment records its data provenance and cutoff

#### Scenario: Future price is needed for an outcome
- **WHEN** a completed forward horizon is evaluated
- **THEN** the outcome stage uses the next decision-eligible adjusted close for entry and the declared later adjusted close for exit without exposing those prices to factor construction

#### Scenario: Historical universe membership is unavailable
- **WHEN** the engine cannot establish that an ETF belonged to the eligible universe on signal date T
- **THEN** the sample is excluded with a point-in-time membership reason

#### Scenario: Only ineligible price data exists
- **WHEN** a required input or outcome is raw, stale, estimated, fallback-only, or lacks required adjustment provenance
- **THEN** the sample is excluded and no substitute value is used

### Requirement: Primary Comparison Is Paired Top Ten Five-Session Net Excess
The system SHALL use the manifest-frozen paired difference between candidate and baseline Top 10 five-session net returns as the primary endpoint.

#### Scenario: Primary sample is calculated
- **WHEN** baseline and candidate are both valid on a rebalance date
- **THEN** they use the same eligible date and common-support ETF panel, enter at the next eligible adjusted close, exit five sessions later, and deduct the declared non-zero turnover costs

#### Scenario: Candidate changes sample coverage
- **WHEN** a candidate requires additional history or excludes an ETF
- **THEN** the paired primary comparison uses common support and separately reports candidate and baseline all-available coverage

#### Scenario: Another horizon performs better
- **WHEN** a Top 5/20 or 1/3/10-session secondary result exceeds the primary result
- **THEN** the system reports it as secondary and MUST NOT replace the primary endpoint

### Requirement: Factor Diagnostics Measure Incremental Information
The system SHALL report factor predictiveness, redundancy, portfolio outcomes, implementation costs, stability, and exclusions separately.

#### Scenario: Cross-sectional factor is evaluated
- **WHEN** a signal date has enough peers in a declared bucket
- **THEN** the system records Spearman rank IC, peer count, quantile returns, top-minus-bottom spread, and 1/3/5/10-session outcomes

#### Scenario: Portfolio candidate is evaluated
- **WHEN** a candidate score produces ranked portfolios
- **THEN** the system records Top 5/10/20 gross and net return, turnover, rank churn, drawdown, concentration, and data coverage

#### Scenario: Factor overlaps baseline
- **WHEN** a candidate factor is highly correlated with baseline components
- **THEN** the report includes factor correlation and marginal contribution rather than counting the same exposure as independent evidence

#### Scenario: Factor excludes samples
- **WHEN** a candidate has missing values, insufficient history, small peer groups, or non-finite outputs
- **THEN** the report records exclusion counts and reasons by date, ETF, history tier, and peer bucket

### Requirement: Sector And Long-History Candidates Are Tested Incrementally
The system SHALL test sector trend after controlling for technical momentum and SHALL test 120/250-session candidates without assuming that longer history improves the baseline.

#### Scenario: Sector trend is tested
- **WHEN** sector trend and technical momentum are available on the same date and peer universe
- **THEN** the system reports sector trend's residual rank IC and paired portfolio contribution after controlling for technical momentum

#### Scenario: Longer-history factor is tested
- **WHEN** a 120-session or 250-session candidate is evaluated
- **THEN** the report compares it with the 61-session baseline on common support and breaks out 120-249 and 250-plus history tiers

#### Scenario: Longer-history factor only improves coverage-selected dates
- **WHEN** improvement disappears on common support or is confined to one declared tier
- **THEN** the system marks the result unstable and does not qualify it for a production proposal

### Requirement: Factor Validation Uses Purged Chronological Splits
The system SHALL use pre-declared chronological development, validation, and locked holdout partitions with expanding walk-forward validation, overlapping-label purge, and a 10-session embargo.

#### Scenario: Walk-forward fold is evaluated
- **WHEN** a development or validation fold is run
- **THEN** its training dates precede its evaluation dates and overlapping forward-label dates are purged

#### Scenario: Split boundary is crossed
- **WHEN** samples lie within the declared embargo around a partition boundary
- **THEN** those samples are excluded from model or candidate selection and the exclusion count is reported

#### Scenario: Holdout is requested
- **WHEN** the final holdout has not been consumed and manifest, code version, candidates, and non-holdout evidence are frozen
- **THEN** the system permits one holdout calculation and records its consumption

#### Scenario: Holdout-driven retry is requested
- **WHEN** a user changes a candidate or threshold after seeing holdout outcomes
- **THEN** the prior holdout can no longer validate the new experiment and the system requires a new future holdout

### Requirement: Factor Evidence Reports Uncertainty And Multiple Testing
The system SHALL report date-block-bootstrap uncertainty and apply the manifest-declared multiplicity control across no more than three primary candidate comparisons.

#### Scenario: Primary interval is calculated
- **WHEN** enough paired holdout samples exist
- **THEN** the system reports the paired net-excess estimate, block-bootstrap interval, adjusted significance state, block rule, and sample count

#### Scenario: Samples are insufficient
- **WHEN** paired dates, regimes, or coverage do not meet the manifest minimum
- **THEN** the result is `insufficient_data` and MUST NOT be promoted from a point estimate

#### Scenario: Candidate passes only unadjusted testing
- **WHEN** a candidate appears positive before multiplicity control but fails the declared adjusted gate
- **THEN** the report marks it unconfirmed

### Requirement: Factor Evidence Cannot Mutate Production
The system SHALL persist factor experiment results as research-only evidence and SHALL NOT automatically modify ranking formulas, weights, caps, labels, allocation, tracked positions, alerts, or notifications.

#### Scenario: Candidate passes every promotion gate
- **WHEN** the adjusted primary holdout interval, fold and regime stability, coverage, turnover, drawdown, concentration, and exclusion gates all pass
- **THEN** the system may label it `eligible_for_v4_proposal` but makes no production change

#### Scenario: Candidate fails or evidence is insufficient
- **WHEN** any required gate fails or lacks evidence
- **THEN** current ranking and downstream behavior remain unchanged

### Requirement: Factor Experiments Are Bounded And Resumable
The system SHALL execute factor experiments with one worker, batches of at most 20 ETFs per history fetch, checkpointed date cursors, cached factor rows, and command or data-operation timeouts of at most 55 seconds.

#### Scenario: Experiment is interrupted
- **WHEN** a batch fails or reaches its timeout
- **THEN** the run persists a reproducible error summary and cursor without launching a concurrent full run

#### Scenario: Experiment resumes
- **WHEN** the same manifest and code version resume from a checkpoint
- **THEN** completed batches are reused idempotently and their hashes remain unchanged

### Requirement: Leader-tactics proxies prove separate incremental alpha
The factor-validation system SHALL evaluate the leader-tactics hypothesis registry as a separate pre-registered factor experiment against the current frozen Top10 baseline and SHALL NOT add its candidates to or replace the existing baseline, hysteresis, and regime/liquidity ranking-candidate registry.

#### Scenario: Leader experiment starts
- **WHEN** the exact leader-tactics registry, baseline contract, split, costs, exclusions, uncertainty method, and promotion gates are frozen
- **THEN** the existing common-support factor experiment may evaluate up to the three declared leader candidates under a distinct manifest and holdout identity

#### Scenario: Existing ranking experiment is resumed
- **WHEN** the baseline/hysteresis/regime-liquidity research loop resumes
- **THEN** its candidate registry and hashes remain unchanged by the leader-tactics experiment

### Requirement: Leader-tactics overlap and scarcity are decision gates
The factor-validation system SHALL report each leader candidate's residual relationship to existing momentum, sector-trend, risk, liquidity, and overextension factors, plus signal frequency, qualifying ETFs per date, complete Top10 dates, history-tier coverage, peer-mapping coverage, clone exclusions, regime dependence, and concentration.

#### Scenario: Proxy repeats existing momentum
- **WHEN** raw candidate performance is favorable but residual IC or marginal common-support contribution after existing factor controls is not positive and stable out of sample
- **THEN** the candidate is `unconfirmed` and cannot become promotion eligible

#### Scenario: Signal set cannot form Top10
- **WHEN** fewer than ten eligible non-clone ETFs have a finite candidate score on a signal date
- **THEN** the date is excluded from the primary comparison with `insufficient_candidate_cohort` and MUST NOT be filled from the baseline or another proxy

#### Scenario: Result depends on one regime or small ETF subset
- **WHEN** improvement is confined to one short regime, one peer group, one history tier, or a small set of ETFs
- **THEN** the stability gate fails even if the pooled point estimate is positive

### Requirement: Leader-tactics promotion uses existing hard evidence gates
A leader-tactics candidate SHALL remain research-only unless it passes the existing paired Top10 five-session net-excess endpoint, common-support coverage, at least 252 factual point-in-time sessions, at least 40 non-overlapping primary dates, at least three chronological folds, purge and embargo, Holm-adjusted block-bootstrap interval, one-time holdout, turnover, drawdown, concentration, clone, finite-value, and raw-price gates.

#### Scenario: Exploratory metric is stronger
- **WHEN** MA5 lifecycle, Top5 or Top20, absolute return, hit rate, or a 1, 3, or 10-session result is favorable but the primary gate fails
- **THEN** the evidence remains `unconfirmed` or `rejected` and MUST NOT be described as validated alpha

#### Scenario: Every gate passes
- **WHEN** a frozen candidate passes every existing promotion gate on its one-time holdout
- **THEN** it may be labeled `eligible_for_v4_proposal` but does not modify production until a separate manually approved score-version change

### Requirement: Factor Peer Support Uses The Weakest Required Primitive
Factor diagnostics SHALL report per-primitive non-clone peer counts and same-date common-support counts, and SHALL treat the support of a composite factor as no greater than the minimum support of its required primitives. Formal `final_score_v3` score behavior remains frozen; stricter support is a research qualification gate until separately promoted.

#### Scenario: One primitive has fewer peers
- **WHEN** a composite candidate has 100 peers for momentum but only 8 peers for breadth
- **THEN** its common support is at most 8 and the report MUST NOT claim 100 eligible peers for the composite

#### Scenario: Shadow peer support is too small
- **WHEN** a date and peer bucket contains fewer than the frozen 20 non-clone common-support ETFs
- **THEN** that bucket-date is excluded from candidate IC and portfolio evidence with `insufficient_common_peer_support`

#### Scenario: Formal score is evaluated
- **WHEN** the stricter shadow peer-support gate is introduced
- **THEN** current `final_score_v3` weights, contract hash, production ranks, and downstream actions remain unchanged

### Requirement: Factor Redundancy Is Measured Before Incremental Alpha
The factor experiment SHALL calculate same-date, same-peer-bucket pairwise Spearman correlations, deterministic correlation clusters, effective factor count, baseline correlation, residual IC, and common-support marginal contribution for every frozen candidate.

#### Scenario: Candidate repeats existing price exposure
- **WHEN** a candidate is highly correlated with existing momentum, risk, liquidity, or sector primitives
- **THEN** the report assigns its correlation cluster and requires positive stable residual evidence instead of treating the raw factor as independent alpha

#### Scenario: Candidate has raw IC but no residual IC
- **WHEN** raw IC is favorable but residual IC or common-support marginal contribution is non-positive or unstable out of sample
- **THEN** the candidate remains `unconfirmed` regardless of its raw point estimate

#### Scenario: Diagnostics are sparse
- **WHEN** fewer than 20 non-clone common-support observations exist for a bucket-date
- **THEN** correlation and residual statistics for that unit are unavailable rather than zero-filled or pooled across later dates

### Requirement: Deconfounding Candidates Are Frozen And Research Only
The system SHALL pre-register at most three candidates under a separate immutable deconfounding registry: the current daily baseline, a residual-momentum plus breadth candidate, and a PIT flow plus constituent-breadth candidate. Missing authoritative PIT inputs SHALL make the affected candidate unavailable without falling back to baseline values.

#### Scenario: Registry is created
- **WHEN** the deconfounding experiment starts
- **THEN** candidate IDs, formulas, directions, transforms, minimum 20-peer support, required history, missing-value rules, and registry hash are frozen before outcomes are read

#### Scenario: Flow data is unavailable
- **WHEN** authoritative point-in-time ETF share-change or constituent-breadth input is absent at the signal cutoff
- **THEN** the PIT flow candidate is unavailable for that ETF-date and the baseline score is not substituted

#### Scenario: More candidates are requested
- **WHEN** a runtime request supplies a fourth candidate or changes a frozen formula after outcomes exist
- **THEN** the experiment is rejected and cannot reuse prior evidence or holdout authorization

#### Scenario: Candidate passes preliminary diagnostics
- **WHEN** a candidate has positive development or validation evidence
- **THEN** it remains research-only and MUST NOT change production ranking until all existing promotion gates pass and a separate manually approved score-version change is implemented
