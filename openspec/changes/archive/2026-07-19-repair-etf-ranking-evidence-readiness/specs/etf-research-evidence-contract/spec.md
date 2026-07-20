## ADDED Requirements

### Requirement: Ranking source provenance is orthogonal and non-mergeable
The evidence contract SHALL keep production publication, research replay, policy mode, notification provenance, and execution provenance as independent dimensions and MUST NOT merge evidence across ranking source kinds.

#### Scenario: Production and replay use the same forward prices
- **WHEN** a production-published cohort and a research-replay cohort share outcome dates or adjusted-price rows
- **THEN** their sample counts, coverage, intervals, confidence, and evidence status remain in separate evidence groups

#### Scenario: Source kind is missing
- **WHEN** a validation summary lacks a registered ranking source kind
- **THEN** the evidence builder classifies it as unregistered/legacy rather than inferring a source from current configuration

### Requirement: Historical provenance repair is factual and conditional
The system SHALL backfill a missing validation source kind only when immutable linked records prove the unique source kind and all persisted contract identities agree.

#### Scenario: Legacy row has a factually complete source set
- **WHEN** a legacy validation aggregate is factually proven to contain exactly one source date with its one exact published full v3 event, or every date of a multi-date aggregate can be reconstructed into a complete compatible immutable manifest
- **THEN** a dry audit may propose and an explicit migration may persist `production_published` with the source-event count and manifest hash

#### Scenario: Multi-date aggregate links only one snapshot
- **WHEN** a legacy aggregate covers multiple dates but stores only one published snapshot link or cannot reconstruct any source event
- **THEN** it remains legacy/unregistered even when that one linked snapshot is individually compatible

#### Scenario: Legacy row is ambiguous
- **WHEN** a validation row is partial, hashless, mismatched, linked to multiple possible sources, or lacks immutable replay identity
- **THEN** the row remains legacy and no source kind or current hash is synthesized

### Requirement: Evidence observations carry environment attribution
The system SHALL associate operational evidence observations with non-secret environment and deployment fingerprints and SHALL prevent observations from different environments from satisfying one production rollout session.

#### Scenario: Local and VPS observations share a trade date
- **WHEN** local and production instances record readiness for the same exchange date
- **THEN** only observations matching the configured production attestation can count toward production session gates

#### Scenario: Same production session is observed repeatedly
- **WHEN** the attested production instance is inspected multiple times for one trade date
- **THEN** those observations remain one exchange session and do not inflate independent-session counts

### Requirement: Production validation evidence is reproducible below the aggregate level
The system SHALL retain an immutable production source event per signal date and an auditable sample or exclusion record per ETF and horizon before deriving aggregate validation metrics.

#### Scenario: Production sample completes
- **WHEN** a production-published ETF observation has valid T+1 entry and exit legs
- **THEN** evidence retains source-event identity, signal date, ETF identity, rank and score identity, entry and exit sessions and adjusted values, execution-cost contract, gross and net return, and immutable sample hash

#### Scenario: Production sample does not complete
- **WHEN** an observation is pending, overlapping, outside the point-in-time universe, or missing a required adjusted-price leg
- **THEN** evidence retains the exact stable reason and source identity rather than dropping the observation from the denominator

#### Scenario: Aggregate is reproduced
- **WHEN** an authorized audit rebuilds a validation summary from immutable source events and samples
- **THEN** counts, coverage, paired dates, returns, intervals, drawdown metrics, and exclusions match the persisted aggregate hash

### Requirement: Rollout sessions do not substitute for statistical evidence
Operational rollout acceptance and return-validation sufficiency SHALL be reported as separate gates.

#### Scenario: Three production rollout sessions pass
- **WHEN** adjusted coverage and publication checks pass on three distinct attested production trade dates
- **THEN** the rollout session gate may pass, but formal return evidence remains insufficient until its separately required 20 completed non-overlapping paired dates and coverage gates pass
