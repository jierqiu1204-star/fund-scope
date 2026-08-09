## MODIFIED Requirements

### Requirement: ETF point-in-time research loop is bounded and resumable
The system SHALL coordinate replay inputs, Stage A/B, frozen candidates, forward outcomes, ranking validation, factor evidence, policy shadow, and final evidence through one single-worker workflow whose individual production continuation advances at most one durable page and returns within 55 seconds.

#### Scenario: A later phase becomes next
- **WHEN** Stage B is complete and the immutable candidate artifact prerequisites pass
- **THEN** the production composition advances the candidate phase instead of returning a permanently unavailable placeholder

#### Scenario: Future outcome is pending
- **WHEN** a candidate exists but the exact next-session entry or declared exit session has not completed
- **THEN** the outcome phase records a pending window and does not shift, estimate, or backfill the execution date

#### Scenario: Bounded continuation has remaining work
- **WHEN** a continuation reaches its time or resource budget before its current phase completes
- **THEN** it commits the last complete idempotent page, records remaining work, releases its lease, and returns without starting another continuation

#### Scenario: Interrupted run resumes
- **WHEN** the same immutable run identity is continued after interruption
- **THEN** it resumes from the durable phase and cursor without duplicating artifacts, samples, aggregates, policy events, or holdout state

### Requirement: Research loop uses only factual point-in-time decision data
The system SHALL use authoritative historical membership and the latest compatible append-only adjusted-price revision recorded as available no later than each signal cutoff.

#### Scenario: Adjusted row was first observed later
- **WHEN** an adjusted value exists for a historical trade date but its immutable first-seen time is after the signal cutoff
- **THEN** the row is excluded with a point-in-time visibility reason and is not used to construct a score or outcome

#### Scenario: Adjusted row has multiple revisions
- **WHEN** more than one compatible revision exists for a code/date
- **THEN** replay selects the latest revision whose observed-at time is no later than the visibility cutoff and records its revision hash

#### Scenario: Current projection is refreshed
- **WHEN** a bounded sync updates the current code/date projection
- **THEN** immutable first-seen and prior revision facts are not overwritten

#### Scenario: Existing row lacks factual receipt evidence
- **WHEN** a legacy adjusted row has no provable historical first-seen or revision cutoff
- **THEN** it may support current production if otherwise eligible but receives no synthetic historical PIT credit

## ADDED Requirements

### Requirement: Operational and statistical readiness are separate
The system SHALL expose `ranking_ready` independently from `research_ready` and SHALL NOT infer predictive validation from current-date publication coverage.

#### Scenario: Current ranking gates pass
- **WHEN** the target-date 95-percent adjusted-data and 90-percent warm-up gates pass
- **THEN** `ranking_ready` may be true even while PIT samples, folds, or holdout evidence are insufficient

#### Scenario: Statistical evidence is insufficient
- **WHEN** fewer than 252 factual PIT sessions, 40 non-overlapping primary dates, or three chronological folds exist
- **THEN** `research_ready` is false with exact counts and current production weights remain frozen

#### Scenario: Every research gate passes
- **WHEN** the frozen experiment passes sample, common-support, uncertainty, multiplicity, turnover, drawdown, concentration, clone, finite-value, raw-price, fold, regime, and one-time holdout gates
- **THEN** evidence may be marked promotion-eligible but still requires a separate manually approved production change

### Requirement: Every production PIT phase persists immutable artifacts
The system SHALL persist each completed phase artifact outside the mutable continuation checkpoint and bind it to manifest, code, input, cutoff, candidate, outcome, cost, and predecessor hashes.

#### Scenario: Phase page completes
- **WHEN** a phase completes one bounded page
- **THEN** its artifact and cursor commit atomically and a retry produces the same artifact hash

#### Scenario: Production side effects are inspected
- **WHEN** any PIT phase advances or completes
- **THEN** production ranking, allocation, positions, alerts, notification logs, SMTP state, execution receipts, score weights, and unrelated holdout identities remain unchanged
