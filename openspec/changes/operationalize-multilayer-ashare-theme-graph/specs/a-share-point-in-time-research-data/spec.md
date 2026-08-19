## ADDED Requirements

### Requirement: Industry, theme membership, and theme state remain separate PIT layers
The A-share research input contract SHALL expose one selected primary-industry path, every cutoff-visible theme membership, and independently materialized theme-state facts. It SHALL NOT collapse multi-label membership at ingestion time or substitute a current concept constituent set for a missing historical fact.

#### Scenario: Multiple theme facts are visible
- **WHEN** an A-share input belongs to multiple compatible themes by the cutoff
- **THEN** all membership hashes are included in the input evidence and deterministic resolution operates on the full set

#### Scenario: Theme-state fact arrives late
- **WHEN** a daily theme-state fact is first received after the decision cutoff
- **THEN** that state is excluded from the historical decision while the underlying membership facts remain independently queryable

### Requirement: Hierarchy source compatibility is enforced
The system SHALL compare peers only inside one declared taxonomy and hierarchy level and SHALL NOT merge source-specific fallback groups into a more specific industry or theme cohort.

#### Scenario: Sources have different granularity
- **WHEN** one security has a level-three path and another has only a broad fallback label
- **THEN** they are not treated as members of the same fine-grained peer group unless a factual shared ancestor is explicitly resolved

