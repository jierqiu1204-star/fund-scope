## ADDED Requirements

### Requirement: Exit Signals Are Classified By Policy Purpose
The system SHALL classify ETF exit signals by their policy purpose before evaluating their credibility.

#### Scenario: Hard stop is classified as insurance
- **WHEN** the signal type is `hard_stop`
- **THEN** the validation result SHALL classify it as `insurance_stop`

#### Scenario: Trailing take profit is classified as profit protection
- **WHEN** the signal type is `trailing_take_profit` or `take_profit_watch`
- **THEN** the validation result SHALL classify it as `profit_protection`

#### Scenario: Trend weakening is classified as guard
- **WHEN** the signal type is `trend_weakening`
- **THEN** the validation result SHALL classify it as `trend_guard` unless another actionable loss or giveback signal confirms it

### Requirement: Exit Validation Uses Policy-Specific Metrics
The system SHALL evaluate ETF exit signals using metrics that match the signal's policy purpose instead of one shared future-direction win rate.

#### Scenario: Hard stop credibility
- **WHEN** an insurance stop is validated
- **THEN** the result SHALL include tail-loss and drawdown-control metrics

#### Scenario: Profit protection credibility
- **WHEN** a profit protection signal is validated
- **THEN** the result SHALL include high-water profit retention, giveback avoided, and missed-upside metrics

#### Scenario: Guard credibility
- **WHEN** a trend guard is validated
- **THEN** the result SHALL include guard-only outcome metrics and SHALL NOT mark it as a standalone sell prediction

### Requirement: Evidence Status Blocks Misleading Conclusions
The system SHALL mark ETF exit evidence as unavailable or research-only when the evidence cannot prove the current live rule.

#### Scenario: Old contract evidence
- **WHEN** the evidence contract hash or rule version does not match the current live rule
- **THEN** the result SHALL be marked `old_contract` or equivalent and SHALL NOT be presented as current live evidence

#### Scenario: Insufficient samples
- **WHEN** sample count is below the configured minimum
- **THEN** the result SHALL be marked `insufficient` and SHALL NOT produce a strong positive or negative conclusion

#### Scenario: Candidate parameter evidence
- **WHEN** evidence belongs to an unapproved optimized candidate
- **THEN** the result SHALL be marked `candidate_research_only` and SHALL NOT affect live email rules

### Requirement: Exit Policy Evidence Is Read-Only
The ETF exit policy validation system SHALL NOT send notifications, create real alerts, or mutate tracked positions.

#### Scenario: Validation run
- **WHEN** an ETF exit policy validation or calibration run executes
- **THEN** it SHALL write only research evidence and SHALL NOT create notification logs or tracked-position alert records

### Requirement: Strategy Evidence Page Separates Live Rules From Research Evidence
The UI SHALL separate current live ETF exit rules from guard-only states and research-only optimized candidates.

#### Scenario: Viewing strategy evidence
- **WHEN** the user opens the strategy evidence page
- **THEN** the page SHALL show current live rule status separately from candidate optimized rule status

#### Scenario: Guard-only trend warning
- **WHEN** a trend weakening result is guard-only
- **THEN** the UI SHALL label it as a warning/guard and SHALL NOT describe it as a direct sell instruction
