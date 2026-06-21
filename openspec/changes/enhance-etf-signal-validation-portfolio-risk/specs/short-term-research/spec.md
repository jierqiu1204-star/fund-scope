## ADDED Requirements

### Requirement: Short-term ETF cards expose validation evidence
The short-term research UI SHALL show whether each ETF label has sufficient validation evidence when validation data is available.

#### Scenario: Validated label is displayed
- **WHEN** a user views an ETF with validation evidence
- **THEN** the card or detail panel shows sample count, horizon summary, and confidence state

#### Scenario: Validation is insufficient
- **WHEN** validation evidence is insufficient for the ETF's label combination
- **THEN** the UI shows `样本不足` and does not present the label as historically reliable

### Requirement: Short-term ETF detail explains optimized observation weight
The short-term ETF detail panel SHALL explain an ETF's optimized observation weight when it is included in the observation portfolio.

#### Scenario: ETF has optimized weight
- **WHEN** an ETF is part of the latest optimized observation portfolio
- **THEN** the detail panel shows weight, cap constraints, risk factors, and reason

#### Scenario: ETF is excluded from optimized portfolio
- **WHEN** an ETF is not assigned weight because of data, liquidity, correlation, or theme constraints
- **THEN** the detail panel shows the exclusion reason
