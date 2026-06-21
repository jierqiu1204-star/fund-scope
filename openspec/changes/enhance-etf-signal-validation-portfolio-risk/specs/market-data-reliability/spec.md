## ADDED Requirements

### Requirement: Reliability gates apply to validation and optimization
Market data reliability SHALL determine whether data may be used for signal validation, observation portfolio optimization, and adaptive exit reminders.

#### Scenario: Data is verified or alternate provider
- **WHEN** data is marked `verified` or `alternate_provider`
- **THEN** it may be used for validation, optimization, and reminders if freshness rules are also satisfied

#### Scenario: Data is estimated or stale
- **WHEN** data is marked `estimated` or `stale`
- **THEN** it may be displayed for context but MUST NOT be used for validation outcomes, optimized weights, or reminder emails

#### Scenario: Data is unavailable
- **WHEN** required data is unavailable
- **THEN** the system returns an explicit unavailable state instead of filling numeric outputs with zero or stale values
