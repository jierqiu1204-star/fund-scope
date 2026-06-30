## ADDED Requirements

### Requirement: Portfolio Eligibility Uses Dynamic Threshold Context
The ETF observation portfolio optimizer SHALL use dynamic threshold context when deciding whether an ETF can receive target weight.

#### Scenario: ETF is overextended relative to itself
- **WHEN** an ETF's recent move is extreme versus its own dynamic threshold or historical percentile
- **THEN** the optimizer excludes it from target weight or marks it watch-only with a dynamic overextension reason

#### Scenario: ETF move is normal for its volatility
- **WHEN** an ETF has a favorable observation score and its current move is within its own dynamic volatility band
- **THEN** the optimizer may consider it eligible subject to existing data, liquidity, correlation, theme, and weight constraints

### Requirement: Portfolio Excludes High-Premium ETFs
The ETF observation portfolio optimizer SHALL prevent high-premium or extreme-premium ETFs from receiving target weight when premium data is trustworthy.

#### Scenario: High-premium ETF ranks highly
- **WHEN** a high-scoring ETF has trustworthy high premium above the configured portfolio gate
- **THEN** the optimizer assigns it zero target weight and records high premium as the exclusion reason

#### Scenario: Premium data unavailable
- **WHEN** a cross-border or structure-sensitive ETF lacks usable premium data
- **THEN** the optimizer treats it conservatively and records the missing premium context rather than assuming the ETF is safe

### Requirement: Portfolio Explanations Include Dynamic Inputs
Observation portfolio results SHALL explain how dynamic thresholds affected included, watch-only, and excluded ETFs.

#### Scenario: ETF receives target weight
- **WHEN** an ETF receives non-zero observation weight
- **THEN** the response includes dynamic threshold summary, volatility unit, score, liquidity, correlation, theme, and premium context where available

#### Scenario: ETF is watch-only or excluded
- **WHEN** an ETF is not assigned target weight because of dynamic overextension, premium risk, weak entry timing, or insufficient dynamic context
- **THEN** the response includes a readable explanation that can be shown in the UI
