## ADDED Requirements

### Requirement: Intraday watch scope audit
The intraday ETF watch job SHALL expose why each watched ETF is included in the watch scope.

#### Scenario: Watchlist source returned
- **WHEN** the intraday watch job completes
- **THEN** the result includes sources such as top20 signal, short/high observation label, and tracked position for each watched ETF

### Requirement: Intraday quote freshness audit
The intraday ETF watch job SHALL expose whether quotes are fresh enough for decisions.

#### Scenario: Fresh quote eligible
- **WHEN** a watched ETF has a fresh intraday quote
- **THEN** the job marks it decision-eligible for intraday alert evaluation

#### Scenario: Stale quote blocked
- **WHEN** a watched ETF has stale or missing quote time
- **THEN** the job marks it display-only or unavailable and does not use it for email decisions
