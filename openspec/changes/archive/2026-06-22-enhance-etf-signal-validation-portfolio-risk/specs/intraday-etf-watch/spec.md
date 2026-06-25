## ADDED Requirements

### Requirement: Intraday watch uses validation and portfolio scope without weakening reliability gates
The intraday ETF watch SHALL prioritize watched ETFs from tracked holdings, top validated candidates, short/high observation labels, and optimized observation portfolio candidates, while preserving market session and data reliability gates.

#### Scenario: Trading session is open
- **WHEN** the market is open and fresh quotes are available
- **THEN** the intraday watch updates eligible ETFs and records live score changes

#### Scenario: Validation data is unavailable
- **WHEN** validation data is missing or insufficient
- **THEN** the intraday watch may still monitor configured scope but must mark validation confidence as unavailable

#### Scenario: Quote is stale
- **WHEN** an ETF quote is stale or missing quote time
- **THEN** the intraday watch does not use it for live decision-eligible alerts
