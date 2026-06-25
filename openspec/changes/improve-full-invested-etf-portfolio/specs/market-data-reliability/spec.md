## ADDED Requirements

### Requirement: Full-Invested Portfolio Uses Decision-Eligible Data Only
The system SHALL use only decision-eligible verified or alternate-provider market data when generating full-invested ETF observation portfolio weights.

#### Scenario: Candidate has verified data
- **WHEN** an ETF candidate has sufficient verified or alternate-provider daily history, liquidity data, and reliability metadata
- **THEN** it may be used in weight generation if other constraints allow

#### Scenario: Candidate has fallback or stale data
- **WHEN** an ETF candidate only has fallback, estimated, stale, display-only, or incomplete data
- **THEN** it MUST NOT receive target portfolio weight and the exclusion reason is recorded

### Requirement: Full-Invested Portfolio Does Not Fabricate Missing Exposure
The system SHALL prefer an unavailable portfolio state over filling 100% weights with unreliable or placeholder data.

#### Scenario: Eligible assets are insufficient
- **WHEN** fewer than the configured minimum number of ETF candidates have decision-eligible data
- **THEN** the system does not fabricate weights and returns an explicit data-insufficient state

#### Scenario: Rounding leaves small residual
- **WHEN** valid target weights are rounded for display
- **THEN** any residual is handled as rounding context and not as an unexplained cash allocation
