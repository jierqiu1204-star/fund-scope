## ADDED Requirements

### Requirement: ETF adjusted history accumulates in prioritized bounded lanes

The system SHALL preserve target-date and 61-session publication priority, SHALL
require 95 percent target-date decision-data coverage and 90 percent 61-session
score-warmup coverage before publication or research-depth work, SHALL keep
300/500-session research-depth completion at 95 percent, and SHALL treat
500-session depth as lower-priority non-authoritative telemetry.

#### Scenario: Publication prerequisites are incomplete

- **WHEN** target-date decision-eligible adjusted coverage is below 95 percent
  or 61-session score-warmup coverage is below 90 percent
- **THEN** research-depth provider work does not start and the existing
  publication continuation retains priority

#### Scenario: Warmup coverage is degraded but publishable

- **WHEN** target-date coverage is at least 95 percent and 61-session score
  coverage is at least 90 percent but below 95 percent
- **THEN** the eligible ETF subset may publish and research-depth work may start
- **AND** the snapshot is marked `degraded` while ETFs lacking 61 sessions
  remain excluded from ranking

#### Scenario: Research depth is incomplete

- **WHEN** both publication gates pass and fewer than the required ETFs have 300
  eligible adjusted sessions
- **THEN** one bounded continuation advances the 300-session lane

#### Scenario: Primary research depth is ready

- **WHEN** 300-session coverage reaches 95 percent
- **THEN** later bounded continuations may accumulate 500-session telemetry
  without changing publication or promotion evidence

### Requirement: Research-depth synchronization remains resource bounded

The system SHALL use one worker, adaptive batches of 5 to 20 ETFs, no more than
500 rows per page, 5,000 rows per slice, 512 MiB RSS, six seconds per provider
attempt, 45 seconds for admission, 55 seconds for durable completion, and 60
seconds for process return.

#### Scenario: Healthy slices increase throughput

- **WHEN** consecutive real slices finish below resource and provider-health
  limits with monotonic checkpoints
- **THEN** the effective batch may increase by five up to twenty without changing
  result identity or coverage facts

#### Scenario: A slice degrades

- **WHEN** timeout, memory pressure, provider circuit, or checkpoint failure is
  observed
- **THEN** the next effective batch is reduced to no fewer than five and no
  concurrent worker is started

### Requirement: Adjusted-history availability is factual and durable

The system SHALL persist provider-observed adjusted-history boundaries and
cooldowns, SHALL keep deferred ETFs in coverage denominators, and SHALL NOT infer
listing dates from returned price history.

#### Scenario: Accepted provider returns too little history

- **WHEN** a provenance-valid adjusted provider returns fewer eligible sessions
  than requested
- **THEN** the system records `source_history_shortfall`, the observed range, and
  a retry-after while leaving the ETF incomplete

#### Scenario: A cooldown is active

- **WHEN** a short-history observation has not reached its retry-after
- **THEN** the ETF is deferred from provider work but remains visible in compact
  blockers and coverage counts

#### Scenario: A new ETF blocks publication depth

- **WHEN** a 61-session publication attempt factually returns too little
  adjusted history
- **THEN** its attempt cursor and cooldown are persisted before later slices
  rotate to other repairable gaps while the new ETF remains in the denominator

#### Scenario: Buffer rows do not satisfy a missing frozen publication session

- **WHEN** an ETF returns at least 61 eligible adjusted rows in the bounded
  request window but one of the frozen 61 publication sessions is absent
- **THEN** the availability observation records the exact required-session
  coverage as insufficient
- **AND** the ETF enters the same bounded cooldown without increasing formal
  publication coverage

#### Scenario: Raw history is deeper

- **WHEN** raw Sina, efinance, intraday, estimated, or display-only rows extend
  earlier than accepted adjusted history
- **THEN** those rows do not change adjusted depth, availability status, or
  readiness coverage
