## MODIFIED Requirements

### Requirement: Post-close ETF publication readiness is coordinated by bounded continuations
The system SHALL coordinate post-close ETF publication under a new immutable readiness-policy version by measuring target-session decision-data freshness and canonical 61-session research coverage separately, running at most one bounded continuation when either coverage is below its gate, and generating a ranking snapshot only after target-session coverage reaches 95 percent and canonical score coverage reaches 90 percent.

#### Scenario: Daily freshness is below the gate
- **WHEN** fewer than 95 percent of the authoritative ETF universe have decision-eligible total-return-adjusted data for the target trade date
- **THEN** the coordinator runs at most one bounded publication-readiness slice and leaves publication in an explicit waiting state

#### Scenario: Daily freshness passes but canonical score coverage does not
- **WHEN** target-date adjusted coverage is at least 95 percent but fewer than 90 percent pass 61-session, absolute-tradability, and taxonomy eligibility
- **THEN** the coordinator reports the separate history, liquidity, and taxonomy blockers and MUST NOT fabricate scores for excluded ETFs

#### Scenario: Both gates pass
- **WHEN** target-date adjusted coverage is at least 95 percent and canonical research coverage is at least 90 percent under the exact current policy version
- **THEN** the coordinator materializes and publication-validates one exact canonical research snapshot without running another synchronization slice

#### Scenario: Policy semantics change
- **WHEN** a threshold, eligible numerator, readiness state, or publication meaning changes
- **THEN** a new policy version is required and persisted historical versions retain their original semantics

## ADDED Requirements

### Requirement: Canonical publication is unique and supersession is explicit
The system SHALL enforce one published row per exact canonical publication identity and SHALL select one current-compatible canonical row per trade date and surface contract.

#### Scenario: Concurrent duplicate publication occurs
- **WHEN** two transactions attempt to publish the same trade date, scope, surface contracts, policy version, universe, inputs, and cutoffs
- **THEN** a database constraint or locked canonical registry permits one winner and the other returns the existing row

#### Scenario: Same results are regenerated under the same identity
- **WHEN** regenerated rows, scores, ranks, and hashes equal an existing exact identity
- **THEN** publication is idempotent and does not create another published run

#### Scenario: A new contract supersedes an old contract
- **WHEN** a new immutable surface or policy contract validly publishes for the same trade date
- **THEN** old evidence remains immutable, the selector exposes one current canonical row, and supersession lineage is recorded

### Requirement: Provider health is sealed with publication
The system SHALL bind the provider-health evidence used for ranking availability to the snapshot draft seal and publication identity.

#### Scenario: Provider health is compatible
- **WHEN** publication validation runs
- **THEN** it verifies provider-health hash, observation time, provider policy version, covered data fields, and source range against the materialized inputs

#### Scenario: Provider health is missing or incompatible
- **WHEN** required health evidence is absent, stale, or belongs to another continuation identity
- **THEN** actionable publication remains unavailable with a stable reason and the missing evidence cannot be replaced by a generic healthy status

### Requirement: Published snapshot state describes available surfaces honestly
The system SHALL distinguish complete research publication from actionable availability.

#### Scenario: Research rows exist and actionable rows are empty
- **WHEN** the 95/90 research gates pass but mandatory intraday execution evidence produces zero actionable rows
- **THEN** the snapshot state is `research_complete_actionable_unavailable`, research selection remains available, and actionable selection returns its exact blocker summary

#### Scenario: Both surfaces have eligible rows
- **WHEN** the research publication gates pass and at least one complete actionable row exists
- **THEN** the snapshot may be labeled `research_complete_actionable_available`
