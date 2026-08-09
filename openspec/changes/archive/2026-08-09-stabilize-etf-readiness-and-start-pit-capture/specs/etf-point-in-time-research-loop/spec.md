## MODIFIED Requirements

### Requirement: ETF point-in-time research loop is bounded and resumable
The system SHALL coordinate replay inputs, ranking stages, frozen candidates, forward outcomes, validation, and policy shadow through one single-worker workflow whose individual continuation is bounded to at most 55 seconds, and production scheduling SHALL advance at most one page only after a complete current-contract dual snapshot exists.

#### Scenario: Bounded continuation has remaining work
- **WHEN** a continuation reaches its time or resource budget before all phases complete
- **THEN** it commits the last complete idempotent page, records remaining work, releases its lease, and returns without starting another continuation

#### Scenario: Interrupted run resumes
- **WHEN** the same immutable run identity is continued after interruption
- **THEN** it resumes from the durable phase and cursor without duplicating samples, aggregates, or policy events

#### Scenario: Concurrent worker attempts the same run
- **WHEN** another worker already holds the run lease
- **THEN** the new worker fails closed without performing provider, replay, or persistence work

#### Scenario: Readiness is blocked or degraded
- **WHEN** the target-date production readiness state is not `complete`
- **THEN** the production PIT scheduler advances no page and records a stable publication-prerequisite reason

#### Scenario: Complete snapshot permits capture
- **WHEN** one complete current-contract dual snapshot exists and the PIT cadence and lease permit work
- **THEN** exactly one bounded PIT page may advance without requesting live providers or mutating production decisions

## ADDED Requirements

### Requirement: Production PIT phase composition is explicit and isolated
The system SHALL compose the existing PIT manifest and phase handlers in the workflow layer, SHALL persist stable unavailable reasons when factual inputs or handlers are unavailable, and MUST NOT embed research algorithms in scheduler, admin, or API modules.

#### Scenario: Production continuation starts
- **WHEN** complete publication and factual PIT input prerequisites pass
- **THEN** the workflow creates or resumes one immutable manifest and invokes the next declared phase handler for one bounded page

#### Scenario: Historical input arrived after its replay cutoff
- **WHEN** a stored adjusted row or membership fact was received after the historical replay visibility cutoff
- **THEN** the phase excludes it with a factual visibility reason and does not increase PIT coverage

#### Scenario: Required phase input is unavailable
- **WHEN** the next PIT phase lacks a required factual input, future window, or compatible contract
- **THEN** the continuation persists a stable unavailable or insufficient-data state without fabricating a sample

#### Scenario: PIT evidence is persisted
- **WHEN** a page completes
- **THEN** only research manifests, checkpoints, samples, aggregates, exclusions, and policy-shadow evidence change while production ranking, allocation, positions, alerts, notification logs, SMTP state, and score weights remain unchanged
