## Purpose

Provide a causal, versioned A-share classification graph that separates stable industry hierarchy, multi-label themes, and daily theme strength so research screens can use comparable peer groups without inventing historical membership.

## ADDED Requirements

### Requirement: Primary industry paths are hierarchical and point-in-time
The system SHALL persist at most one primary industry path per security, taxonomy, and effective session, including level-one, level-two, and level-three identifiers and labels, effective window, receipt time, source, confidence, mapping kind, and immutable fact hash. It SHALL preserve a source-specific fallback path when a complete hierarchy is unavailable and SHALL expose the missing levels.

#### Scenario: Complete industry hierarchy is available
- **WHEN** a compatible level-one through level-three path was received by the decision cutoff
- **THEN** the security is resolved to that immutable path and may use the finest level with sufficient eligible peers

#### Scenario: Only a fallback industry is available
- **WHEN** a security has a factual current industry label but no compatible complete hierarchy
- **THEN** the fallback remains queryable with its own taxonomy and hierarchy level and is not relabeled as a level-three industry or theme

### Requirement: Theme membership is a point-in-time multi-label relation
The system SHALL allow each security to have zero or more simultaneous theme memberships and SHALL persist canonical theme key, provider theme code and label, membership reason when supplied, exposure weight when supplied, relation kind, effective window, snapshot date, receipt time, source, taxonomy version, confidence, and immutable hash for every relation.

#### Scenario: Security belongs to multiple themes
- **WHEN** two compatible theme memberships are visible by the cutoff
- **THEN** both memberships remain queryable and neither is discarded because another theme has higher hard-coded priority

#### Scenario: Current-only concept membership is captured
- **WHEN** a provider exposes only its current constituent set
- **THEN** membership becomes effective no earlier than its factual capture date and cannot be used to reconstruct an earlier PIT recommendation

### Requirement: Theme definitions are canonical and source identities remain distinct
The system SHALL maintain a versioned registry that maps provider labels and codes to canonical theme identities while preserving provider-specific membership facts. Industry-union research proxies SHALL use distinct relation kinds and labels and SHALL NOT be represented as factual provider concepts.

#### Scenario: Aliases describe the same theme family
- **WHEN** provider labels such as `稀土` and `稀土永磁` map to one canonical family
- **THEN** the canonical key is stable while each source label, code, and membership fact remains auditable

#### Scenario: Theme is derived from industry paths
- **WHEN** a curated research theme is defined as a union of disclosed level-three industries
- **THEN** the relation is labeled `industry_union_proxy` and cannot claim factual concept-provider membership

### Requirement: Daily theme state is independent from membership
For each eligible theme session the system SHALL persist a separate state containing eligible member count, up breadth, median one-day and five-day return, relative market return, amount participation, leader count, available limit-board metrics, source cutoff, input hash, availability, and exact unavailable reasons.

#### Scenario: Membership exists but state inputs are incomplete
- **WHEN** a theme has factual members but lacks enough eligible adjusted bars or peers
- **THEN** membership remains available while theme state fails closed with exact numerator, denominator, and unavailable reason

#### Scenario: Complete theme state is recomputed
- **WHEN** identical membership and price facts are processed for the same cutoff
- **THEN** the state hash and metrics are identical and no duplicate state is published

### Requirement: Capture is bounded, resumable, and complete by source snapshot
Industry and theme capture SHALL use deterministic source ordering, bounded requests, durable per-source checkpoints, bounded response sizes, one worker, and a hard invocation budget no greater than 55 seconds. A source snapshot SHALL become selectable only after its declared member count and content hash are complete.

#### Scenario: One source page times out
- **WHEN** a provider request exceeds its bound
- **THEN** completed sources remain committed, the failed source records a retryable health reason, and no partial source snapshot is selected

#### Scenario: Capture resumes
- **WHEN** a later scheduler occurrence resumes a compatible checkpoint
- **THEN** it starts at the first incomplete source or page and produces the same completed content hash as an uninterrupted run

### Requirement: Classification readiness is observable
The read-only research surface SHALL report authoritative-universe denominator, complete industry-path coverage by hierarchy and source, multi-theme member and relation counts, theme-state coverage, latest complete snapshot dates, provider health, stale sources, and fallback counts.

#### Scenario: Fine themes are configured but empty
- **WHEN** no complete theme snapshot has been persisted
- **THEN** the API reports zero factual theme coverage and a stable unavailable reason rather than describing registered definitions as available members

