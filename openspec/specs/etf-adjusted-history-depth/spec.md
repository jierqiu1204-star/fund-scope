# etf-adjusted-history-depth Specification

## Purpose
TBD - created by archiving change extend-etf-adjusted-history-depth. Update Purpose after archive.
## Requirements
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

### Requirement: Publication and research use explicit independent denominators

The system SHALL keep the complete authoritative point-in-time ETF universe as
the publication denominator, SHALL derive 300/500-session research cohorts only
from official SSE/SZSE listing observations visible by the PIT cutoff, and SHALL
block research completion when listing metadata coverage is below 95 percent.

#### Scenario: An ETF is too new for a research horizon

- **WHEN** an authoritative ETF's observed listing date is later than the first
  frozen required session for a 300-session or 500-session lane
- **THEN** the ETF remains in daily and 61-session publication denominators
- **AND** it is reported as structurally unseasoned rather than missing adjusted
  history for that research horizon

#### Scenario: Listing metadata is unavailable

- **WHEN** an authoritative ETF has no official exchange listing observation
- **THEN** the system does not infer one from its earliest price or membership
  record
- **AND** the ETF is reported as unknown listing metadata
- **AND** the research lane cannot be declared complete unless metadata coverage
  across the full universe is at least 95 percent

#### Scenario: Raw dates extend the observed session calendar

- **WHEN** a raw daily row proves an exchange session existed before the
  currently stored adjusted range
- **THEN** that date may enter the frozen shared session calendar
- **BUT** the raw row does not increase any ETF's adjusted-session coverage

### Requirement: Listing evidence and adjusted providers fail closed

The system SHALL persist complete official exchange listing snapshots as
append-only observations, SHALL evaluate them by observation cutoff, and SHALL
use one central adjusted provider/version registry across ingestion, coverage,
and decision readers.

#### Scenario: A listing fact arrives after a historical cutoff

- **WHEN** a valid official listing observation was recorded after the replay data
  cutoff
- **THEN** that replay reports the listing metadata as unavailable
- **AND** it does not backdate the observation or change the historical cohort

#### Scenario: A provider version is not centrally accepted

- **WHEN** an adjusted row names an unknown or mismatched provider, provider
  version, or adjustment version
- **THEN** ingestion may retain it for audit only
- **AND** it cannot increase publication or research coverage

### Requirement: Research backfill is completion-first and gap bounded

The system SHALL prioritize pending seasoned ETFs nearest to the frozen depth
target and SHALL request only the enclosing span of each ETF's missing required
sessions.

#### Scenario: Several seasoned ETFs have incomplete histories

- **WHEN** pending ETFs have different accepted adjusted depths
- **THEN** the runner orders greater depth first, then watchlist priority and
  stable code order, while rotating only within equal-depth buckets

#### Scenario: Only an older segment is missing

- **WHEN** an ETF already stores recent required sessions but lacks an older
  required segment
- **THEN** the provider request begins at the earliest missing required date and
  ends at the latest missing required date instead of refetching the full
  research horizon
- **AND** only rows matching actually missing frozen sessions are persisted

#### Scenario: A slice is interrupted

- **WHEN** the process stops after one or more idempotent page commits
- **THEN** the next slice reconstructs the remaining required dates from
  persisted adjusted rows and resumes without duplicate coverage or an
  in-memory-only checkpoint
