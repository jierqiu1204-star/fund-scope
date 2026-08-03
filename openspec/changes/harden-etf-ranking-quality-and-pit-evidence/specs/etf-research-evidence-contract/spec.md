## ADDED Requirements

### Requirement: Ranking evidence records quality and identity coverage
ETF research evidence SHALL record canonical eligibility policy, absolute tradability threshold, taxonomy version and coverage, unknown-bucket exclusions, tracked-underlying source and coverage, clone groups and representatives, peer-count policy, component bounds, cap violations, non-finite rejects, theme concentration, and observation-only counts.

#### Scenario: Ranking evidence is returned
- **WHEN** the API returns a current research or actionable ranking summary
- **THEN** it includes expected, canonical-ranked, observation-only, actionable, unknown-taxonomy, low-liquidity, unresolved-underlying, clone, cap, and non-finite counts with stable reason codes

#### Scenario: Clone control is claimed
- **WHEN** evidence labels clone policy complete
- **THEN** tracked-underlying coverage meets the declared threshold and every representative choice references cutoff-valid authoritative evidence

#### Scenario: Quality gate changes
- **WHEN** an absolute tradability, taxonomy, peer-count, or clone policy changes
- **THEN** the evidence uses a new policy identity and prior snapshots retain their original interpretation

### Requirement: Ranking evidence records immutable adjusted revisions
ETF research evidence SHALL distinguish current projection time, immutable first-seen availability, selected revision time and hash, market decision cutoff, data receipt cutoff, and replay visibility cutoff.

#### Scenario: Current and PIT evidence differ
- **WHEN** a historical row was backfilled after its trade date
- **THEN** current-production eligibility and historical PIT eligibility are reported separately without inferring earlier visibility

#### Scenario: Revision is selected for replay
- **WHEN** replay uses an adjusted row
- **THEN** evidence records the selected provider, versions, first-seen time, revision observed-at time, revision hash, and visibility cutoff

### Requirement: Publication evidence is versioned and canonical
ETF research evidence SHALL record readiness-policy version, research/actionable contract hashes, surface-group hash, provider-health hash and check time, universe and input hashes, publication identity, supersession lineage, and duplicate-publication status.

#### Scenario: One exact identity publishes
- **WHEN** two workers attempt to publish the same exact canonical identity
- **THEN** exactly one succeeds and evidence records the idempotent winner

#### Scenario: A later contract publishes for the same date
- **WHEN** a materially new current-compatible contract is published for an already published trade date
- **THEN** both immutable rows remain, one current canonical row is selected, and explicit supersession lineage prevents ambiguity

#### Scenario: Research is complete but actionable is empty
- **WHEN** publication gates pass but no ETF has complete same-session execution evidence
- **THEN** evidence states `research_complete_actionable_unavailable` and reports actionable blockers instead of claiming a complete actionable surface

### Requirement: Workbench evidence uses stable user-facing states
The evidence API SHALL distinguish low liquidity, unknown taxonomy, unresolved clone identity, stale snapshot, coverage blocker, no actionable candidates, provider-health unavailable, insufficient PIT history, and contract mismatch.

#### Scenario: Raw reason is persisted
- **WHEN** an internal stable reason code is recorded
- **THEN** the API preserves the code and provides a Chinese user-facing explanation without exposing an ambiguous generic waiting state
