## ADDED Requirements

### Requirement: ETF observation weight explanation
ETF observation portfolio results SHALL include an explanation for every included ETF weight.

#### Scenario: Included ETF explanation
- **WHEN** an ETF receives a non-zero observation weight
- **THEN** the response explains the score, validation confidence, volatility, drawdown, liquidity, correlation, and theme factors that contributed

### Requirement: ETF observation exclusion explanation
ETF observation portfolio results SHALL include concrete reasons for excluded candidates.

#### Scenario: Excluded ETF explanation
- **WHEN** an ETF candidate receives zero weight
- **THEN** the response identifies whether it was excluded for stale data, decision ineligibility, low confidence, high correlation, theme concentration, liquidity, or risk constraints

### Requirement: Weight cap preservation
ETF observation portfolio results SHALL keep the single ETF max weight at 30%.

#### Scenario: Single ETF cap
- **WHEN** observation weights are generated
- **THEN** no individual ETF weight exceeds 30%
