## ADDED Requirements

### Requirement: Short-Term Workbench Shows Research Quality Evidence
The short-term research workbench SHALL display label validation evidence, observation weight reasoning, data reliability, and tracked-position alert explanations when available.

#### Scenario: Selected ETF has validation and portfolio data
- **WHEN** the user selects an ETF with latest validation and optimized portfolio outputs
- **THEN** the detail panel shows label evidence, confidence state, observation weight or exclusion reason, and data reliability

#### Scenario: Evidence is unavailable
- **WHEN** validation or optimized portfolio outputs are missing
- **THEN** the detail panel shows a waiting or unavailable state and keeps the current ranking usable

### Requirement: Short-Term Workbench Explains No-Alert States
The short-term research workbench SHALL explain why a tracked holding has not sent an email when that reason is available.

#### Scenario: Tracked ETF has no alert
- **WHEN** an active tracked ETF has no actionable email signal
- **THEN** the holding card or detail panel shows the main no-alert reason such as threshold not crossed, data ineligible, market closed, or cooldown active

#### Scenario: Tracked ETF has actionable alert
- **WHEN** an active tracked ETF has an actionable alert
- **THEN** the holding card shows alert type, reason, email status, quote time, and relevant threshold context
