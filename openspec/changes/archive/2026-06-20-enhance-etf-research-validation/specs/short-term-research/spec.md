## ADDED Requirements

### Requirement: Short-Term Label Outcomes Are Backtested
The system SHALL calculate historical outcome statistics for each observation label + entry timing label combination using public ETF or fund price history.

#### Scenario: Label outcome statistics are generated
- **WHEN** a short-term signal run is completed and enough future price data exists
- **THEN** the system records sample count, 1/3/5/10 trading-day return, maximum drawdown, win rate, and data coverage for each label combination

#### Scenario: Label outcome lacks enough samples
- **WHEN** a label combination has insufficient completed future windows
- **THEN** the UI marks the validation result as 样本不足 and MUST NOT present the label as historically reliable

### Requirement: Short-Term Research Runs Are Auditable Experiments
The system SHALL persist research experiment metadata for signal generation, label validation, and observation portfolio generation.

#### Scenario: Signal experiment is recorded
- **WHEN** short-term ranking is generated
- **THEN** the system records run id, asset type, universe, data date, parameter version, rule version, item count, and data reliability summary

#### Scenario: User views experiment context
- **WHEN** the user opens /short-term
- **THEN** the UI shows the latest experiment timestamp, data date, and whether label validation is available

### Requirement: ETF Observation Portfolio Uses Risk-Constrained Weights
The system SHALL generate ETF observation portfolio weights using deterministic risk constraints rather than naive equal weights.

#### Scenario: Single ETF weight is capped
- **WHEN** the observation portfolio allocates weight to an ETF
- **THEN** no single ETF target weight exceeds 30%

#### Scenario: Data-ineligible ETF receives no weight
- **WHEN** an ETF has stale, estimated, unavailable, or insufficient data for portfolio construction
- **THEN** it may appear as watch-only but MUST NOT receive target weight

#### Scenario: Similar ETFs are de-duplicated
- **WHEN** two candidate ETFs have high recent return correlation or overlapping theme exposure
- **THEN** the system reduces or excludes one from target weights and explains the concentration reason

#### Scenario: Portfolio remains research-only
- **WHEN** portfolio weights are displayed
- **THEN** the UI labels them as 观察组合参考 and states that they are not automatic buy instructions