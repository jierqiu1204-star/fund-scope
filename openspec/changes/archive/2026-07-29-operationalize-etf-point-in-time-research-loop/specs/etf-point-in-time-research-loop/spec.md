## ADDED Requirements

### Requirement: ETF point-in-time research loop is bounded and resumable
The system SHALL coordinate replay inputs, ranking stages, frozen candidates, forward outcomes, validation, and policy shadow through one single-worker workflow whose individual continuation is bounded to at most 55 seconds.

#### Scenario: Bounded continuation has remaining work
- **WHEN** a continuation reaches its time or resource budget before all phases complete
- **THEN** it commits the last complete idempotent page, records remaining work, releases its lease, and returns without starting another continuation

#### Scenario: Interrupted run resumes
- **WHEN** the same immutable run identity is continued after interruption
- **THEN** it resumes from the durable phase and cursor without duplicating samples, aggregates, or policy events

#### Scenario: Concurrent worker attempts the same run
- **WHEN** another worker already holds the run lease
- **THEN** the new worker fails closed without performing provider, replay, or persistence work

### Requirement: Research loop identity is immutable
The system SHALL bind every research run to the ranking, candidate, cutoff, universe, input, feature, adjusted-price, execution, cost, split, uncertainty, promotion, code-version, and holdout identities used to produce it.

#### Scenario: Material input changes during resume
- **WHEN** a continuation supplies a different immutable manifest field or hash
- **THEN** the system rejects the resume and requires a new run identity

#### Scenario: Batch size changes
- **WHEN** a safe batch size changes between 5 and 20 while immutable inputs remain unchanged
- **THEN** the same run may resume and MUST produce identical research artifacts and hashes

### Requirement: Research loop uses only factual point-in-time decision data
The system SHALL use authoritative historical membership and total-return-adjusted price rows that were recorded as available no later than the signal cutoff.

#### Scenario: Adjusted row was backfilled later
- **WHEN** an adjusted row exists for a historical trade date but its recorded availability is after the signal cutoff
- **THEN** the row is excluded with a point-in-time visibility reason and is not used to construct a score or outcome

#### Scenario: Only raw fallback price exists
- **WHEN** only Sina, efinance, estimated, stale, or otherwise non-decision-eligible price data exists
- **THEN** the sample is excluded and decision coverage is not increased

### Requirement: Research loop uses only three frozen ranking candidates
The system SHALL evaluate the frozen baseline Top10, Top10 hysteresis, and Top10 hysteresis with regime/liquidity gate and MUST reject runtime parameter grids or additional undeclared candidates.

#### Scenario: Candidate set is valid
- **WHEN** the manifest contains the exact frozen candidate registry and at most three primary comparisons
- **THEN** candidate materialization proceeds deterministically

#### Scenario: Undeclared candidate is supplied
- **WHEN** a run adds a candidate, changes hysteresis parameters, or changes a gate after outcomes are available
- **THEN** the run is rejected and cannot consume the existing holdout

### Requirement: Production decisions are isolated from research execution
The system SHALL persist research-loop outputs only as research evidence and MUST NOT update production ranking, allocation, tracked positions, alerts, notification logs, SMTP state, or score weights.

#### Scenario: Research loop completes
- **WHEN** ranking and policy-shadow evidence is persisted
- **THEN** production decision tables and current score contracts remain unchanged
