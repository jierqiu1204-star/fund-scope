## ADDED Requirements

### Requirement: ETF data health reports freshness and depth independently
The system SHALL expose per-universe and per-ETF current-session freshness, 61-session warm-up depth, contract-derived replay depth, optional 180-session operational depth, price basis, provider provenance, and exact exclusions with independent denominators. Adjusted-price depth SHALL NOT claim that production v3 is historically reproducible without compatible point-in-time published snapshots.

#### Scenario: Freshness passes while depth fails
- **WHEN** at least 95 percent of the expected universe is current for the target date but fewer than 95 percent meet the required adjusted-history depth
- **THEN** data health reports the two coverage ratios independently and MUST NOT describe the cohort as fully research-ready

#### Scenario: ETF has incomplete adjusted depth
- **WHEN** one ETF lacks eligible adjusted sessions inside the required exchange-session window
- **THEN** data health reports its available session count, required session count, earliest/latest eligible dates, and exclusion reasons

### Requirement: Historical synchronization health is operationally bounded
The system SHALL report checkpoint identity, last completed code/date unit, remaining candidates, elapsed time, peak memory, provider attempts, circuit state, and continuation status for each historical-depth lane.

#### Scenario: Provider circuit opens
- **WHEN** a provider reaches the configured consecutive bounded failure threshold
- **THEN** the continuation records a reproducible provider error summary, opens the circuit for the declared cooldown, checkpoints progress, and stops repeated calls in that slice

#### Scenario: Resource budget is reached
- **WHEN** elapsed time, rows, or memory reaches its configured budget
- **THEN** the continuation stops normally as `partial` and reports the reached budget rather than appearing hung or failed

#### Scenario: Small-server acceptance profile is verified
- **WHEN** the continuation is benchmarked against PostgreSQL with a production-shaped fixture of at least 1,400 ETFs and 300 eligible sessions per ETF
- **THEN** one slice satisfies the 45-second admission cutoff, 55-second outer worker deadline, 60-second process limit, 768 MiB peak RSS limit, page/row/SQL bounds, atomic cancellation behavior, and no-orphan-process check

### Requirement: Synchronization progress reflects attempted durable work
The system SHALL count a code as processed only after an eligible request attempt and durable result or explicit terminal exclusion for the active lane.

#### Scenario: Code is filtered before provider attempt
- **WHEN** a code selected by the lane is removed by a downstream scope filter
- **THEN** processed and cursor counts remain unchanged and health reports the scope mismatch

#### Scenario: Page commit succeeds
- **WHEN** a code/date page and checkpoint commit atomically
- **THEN** durable progress increases exactly once even if the job response or process is later interrupted
