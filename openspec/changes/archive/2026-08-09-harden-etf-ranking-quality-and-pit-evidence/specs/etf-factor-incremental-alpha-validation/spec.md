## MODIFIED Requirements

### Requirement: Factor Diagnostics Measure Incremental Information
The system SHALL report factor predictiveness, economic overlap, portfolio outcomes, factual daily implementation costs, stability, concentration, and exclusions separately.

#### Scenario: Cross-sectional factor is evaluated
- **WHEN** a signal date has enough non-clone peers in a declared compatible bucket
- **THEN** the system records Spearman rank IC, peer count, quantile returns, top-minus-bottom spread, and 1/3/5/10-session outcomes

#### Scenario: Portfolio candidate is evaluated
- **WHEN** a candidate score produces ranked portfolios
- **THEN** the system records Top 5/10/20 gross and net return, daily turnover, distinct rank churn, drawdown, theme and underlying concentration, quality coverage, and exclusions

#### Scenario: Factor overlaps baseline
- **WHEN** a candidate factor is derived from overlapping returns, sector momentum, risk, or liquidity inputs already represented in the baseline
- **THEN** the report includes residual IC and marginal common-support contribution rather than counting a different primitive name as independent information

#### Scenario: Non-overlapping primary dates are selected
- **WHEN** the primary five-session endpoint excludes overlapping outcome windows
- **THEN** turnover and rank churn still use every factual eligible daily transition between accepted primary dates

#### Scenario: Execution evidence is available
- **WHEN** cutoff-valid spread and liquidity evidence exists for an execution event
- **THEN** the cost report uses the frozen factual cost policy and records its inputs

#### Scenario: Execution evidence is unavailable
- **WHEN** historical spread or impact evidence was not factually available
- **THEN** the frozen conservative fallback cost remains explicit and the system does not reconstruct intraday evidence from later data

## ADDED Requirements

### Requirement: Structural ranking fixes are validated before score promotion
The factor-validation system SHALL compare corrected eligibility, taxonomy, clone, peer, and presentation behavior on common support before proposing any score-weight change.

#### Scenario: Corrected quality universe is evaluated
- **WHEN** the canonical research universe removes low-liquidity or unresolved-taxonomy rows
- **THEN** evidence reports before/after coverage, Top-N membership, turnover, returns, drawdown, theme and underlying concentration, and exclusion composition without attributing the change to factor alpha

#### Scenario: Clone handling is evaluated
- **WHEN** tracked-underlying coverage is sufficient for a clone-aware shadow
- **THEN** evidence reports clone-group coverage, representative selection, Top-N crowding reduction, and common-support outcomes

#### Scenario: Candidate improves only a secondary metric
- **WHEN** a structural or factor candidate improves absolute return, hit rate, concentration, or a secondary horizon but fails the paired Top10 five-session net-excess primary gate
- **THEN** it remains research-only and current production weights remain unchanged

### Requirement: Only the frozen three ranking candidates enter the main PIT comparison
The system SHALL retain the current Top10 baseline, Top10 hysteresis, and Top10 hysteresis with regime/liquidity gate as the only main ranking candidates for this evidence cycle.

#### Scenario: Additional factor idea exists
- **WHEN** theme, catalyst, leader, sentiment, valuation, volume transform, or machine-learning research produces an idea
- **THEN** it uses a separate pre-registered shadow experiment and cannot replace or expand the frozen main candidate registry

#### Scenario: Promotion gates are incomplete
- **WHEN** factual PIT sessions, independent dates, folds, adjusted uncertainty, holdout, turnover, drawdown, concentration, clone, finite-value, or raw-price gates are incomplete
- **THEN** the candidate state is `insufficient_data` and no production score or weight changes
