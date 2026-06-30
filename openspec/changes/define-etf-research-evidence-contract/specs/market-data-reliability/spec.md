## ADDED Requirements

### Requirement: Market data reliability is contract-addressable
Market data reliability SHALL provide explicit states and timestamps that can be referenced by ETF research evidence contracts.

#### Scenario: Verified data is used
- **WHEN** market data is verified or from an accepted alternate provider
- **THEN** the evidence contract may mark it as decision-eligible with provider, timestamp, and freshness metadata

#### Scenario: Stale or estimated data is present
- **WHEN** market data is stale, estimated, display-only, or unavailable
- **THEN** the evidence contract marks it as not decision-eligible and records the reason

#### Scenario: Evidence is generated
- **WHEN** validation or backtest evidence is produced
- **THEN** it records the market data reliability state used for each signal or allocation period
