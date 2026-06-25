## ADDED Requirements

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
