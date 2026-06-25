# etf-observation-portfolio-optimization Specification

## Purpose
TBD - created by archiving change enhance-etf-signal-validation-portfolio-risk. Update Purpose after archive.
## Requirements
### Requirement: Observation portfolio uses constrained optimization
The system SHALL generate ETF observation portfolio weights using decision-eligible assets, ranking scores, volatility, drawdown, liquidity, correlation, and theme concentration constraints.

#### Scenario: Portfolio weights are generated
- **WHEN** enough decision-eligible ETF candidates and price history are available
- **THEN** the system produces a portfolio snapshot with weights, constraints used, excluded assets, and explanation for each weight

#### Scenario: Asset is decision-ineligible
- **WHEN** an ETF has stale, estimated, unavailable, or display-only data
- **THEN** the optimizer assigns it zero weight and records the exclusion reason

### Requirement: Single ETF weight is capped at 30 percent
The system SHALL cap each ETF observation weight at 30%.

#### Scenario: Candidate has dominant score
- **WHEN** one ETF has the highest score by a large margin
- **THEN** its optimized weight does not exceed 30%

### Requirement: Theme concentration is capped
The system SHALL limit total weight assigned to one theme or highly similar theme group.

#### Scenario: Top candidates are all technology ETFs
- **WHEN** the top ranked ETF candidates are concentrated in the same theme
- **THEN** the optimizer reduces theme concentration and records the cap in the snapshot explanation

### Requirement: Correlation reduces duplicate exposure
The system SHALL penalize highly correlated ETF pairs when assigning observation weights.

#### Scenario: Two ETFs are near duplicates
- **WHEN** two eligible ETFs have high return correlation over the configured lookback window
- **THEN** the optimizer reduces combined exposure or excludes the weaker candidate with an explanation

### Requirement: Optimization output is informational
The system SHALL present optimized weights as observation references, not trading instructions.

#### Scenario: User views optimized portfolio
- **WHEN** the optimized observation portfolio is displayed
- **THEN** the UI states that weights are research references and require manual judgment

### Requirement: Optimized Portfolio Has Deterministic Fallback
The ETF observation portfolio optimizer SHALL provide a deterministic fallback when constrained optimization cannot produce a valid solution.

#### Scenario: Optimization is feasible
- **WHEN** eligible ETF candidates have enough return history, volatility, and correlation data
- **THEN** the system returns optimized observation weights with constraints and reasons

#### Scenario: Optimization is not feasible
- **WHEN** covariance, history, candidate count, or constraints make optimization unavailable
- **THEN** the system returns simplified risk-based weights or no-weight output with a clear fallback reason

### Requirement: Observation Portfolio Explains Exclusions
The ETF observation portfolio SHALL explain why eligible ranked ETFs did or did not receive weight.

#### Scenario: ETF receives weight
- **WHEN** an ETF is assigned observation weight
- **THEN** the result includes score contribution, volatility effect, correlation effect, theme cap effect, and final weight

#### Scenario: ETF is excluded
- **WHEN** an ETF is excluded because of data, liquidity, correlation, theme concentration, or weight floor
- **THEN** the result includes the exclusion reason and keeps the asset available as watch-only

