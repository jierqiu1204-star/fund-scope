## ADDED Requirements

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
