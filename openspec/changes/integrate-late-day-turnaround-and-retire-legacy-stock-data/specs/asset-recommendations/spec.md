## ADDED Requirements

### Requirement: Stock recommendation prices use authoritative A-share facts
The system SHALL calculate stock watchlist price metrics from decision-eligible, total-return-adjusted A-share facts with explicit provider, adjustment, and receipt identities, and SHALL NOT create local synthetic price history.

#### Scenario: Authoritative facts are available
- **WHEN** a stock watchlist metric run has sufficient compatible A-share adjusted facts at its cutoff
- **THEN** the system calculates price metrics from those facts and records the source as A-share PIT adjusted evidence

#### Scenario: Authoritative facts are unavailable
- **WHEN** a stock lacks sufficient compatible A-share adjusted facts
- **THEN** the system marks the price-dependent metrics as insufficient data instead of seeding or substituting local price rows

### Requirement: Legacy stock price storage is retired safely
The system SHALL remove the legacy stock price-history schema only after all runtime and test consumers use the authoritative A-share fact source.

#### Scenario: Upgrade removes the old table
- **WHEN** the migration is applied after consumer migration
- **THEN** `stock_price_history` no longer exists while stock universe, fundamental, metric, and recommendation records remain available

#### Scenario: Rollback is required
- **WHEN** the migration is downgraded
- **THEN** the legacy table schema is recreated without changing the authoritative A-share fact tables
