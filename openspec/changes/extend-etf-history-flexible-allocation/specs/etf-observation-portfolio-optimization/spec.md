## ADDED Requirements

### Requirement: ETF observation exposure is a maximum, not mandatory full investment
The system SHALL treat ETF account exposure as a maximum allowed allocation and MUST NOT force the portfolio to be fully invested when too few candidates pass risk and data gates.

#### Scenario: One qualified ETF exists
- **WHEN** exactly one ETF is decision-eligible for allocation
- **THEN** the system assigns no more than the 30% single-ETF cap to that ETF and returns the remaining capital as waiting cash with a readable reason

#### Scenario: Two qualified ETFs exist
- **WHEN** exactly two ETFs are decision-eligible for allocation
- **THEN** the system assigns no more than 60% combined ETF exposure and returns the remaining capital as waiting cash with a readable reason

#### Scenario: Four or more qualified ETFs exist
- **WHEN** four or more decision-eligible ETFs satisfy score, liquidity, risk, theme, and correlation constraints
- **THEN** the system may allocate up to 100% ETF exposure while still respecting the 30% single-ETF cap

### Requirement: Waiting cash is explained as an intentional risk state
The system SHALL explain unallocated ETF account capital as waiting cash instead of presenting it as a data failure.

#### Scenario: Candidate count is low
- **WHEN** some but not enough ETFs pass the allocation gates
- **THEN** the result includes `cash_weight`, `cash_reason`, selected ETF weights, and excluded/watch-only reasons

#### Scenario: No ETF is qualified
- **WHEN** no ETF passes reliability and allocation gates
- **THEN** the system returns `portfolio_mode=cash_wait`, `cash_weight=1.0`, and a reason that no buyable ETF candidate currently qualifies

### Requirement: Partial allocation preserves existing risk constraints
The system SHALL preserve data reliability, liquidity, theme concentration, correlation, and single-ETF cap constraints when producing partial allocations.

#### Scenario: Partial portfolio has concentrated candidates
- **WHEN** qualified ETFs are concentrated in the same theme or highly correlated
- **THEN** the optimizer reduces or excludes duplicate exposure and leaves more waiting cash rather than breaching concentration constraints

#### Scenario: Candidate uses non-decision data
- **WHEN** an ETF has stale, estimated, unavailable, or display-only data
- **THEN** the optimizer assigns zero weight and records the exclusion reason
