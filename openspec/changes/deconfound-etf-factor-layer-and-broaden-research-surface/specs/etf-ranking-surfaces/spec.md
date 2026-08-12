## ADDED Requirements

### Requirement: Research Discovery Eligibility Is Independent From Action Quality
The daily ETF research surface SHALL rank every authoritative-universe ETF with a finite, contract-compatible, point-in-time `daily_reconstructable_v1` score, while independently recording whether liquidity, taxonomy, history depth, and other action-quality gates pass. Failing an action-quality gate SHALL make the ranked row observation-only and non-actionable, but SHALL NOT remove an otherwise valid adjusted-daily research score from discovery.

#### Scenario: Low-turnover ETF has a valid adjusted-daily score
- **WHEN** an ETF has a finite contract-compatible research score but its 20-session average turnover is below the frozen action-quality threshold
- **THEN** the ETF keeps its research rank, is marked observation-only with `absolute_tradability_below_threshold`, and receives no actionable rank

#### Scenario: Taxonomy is unresolved
- **WHEN** an ETF has a finite contract-compatible research score but lacks authoritative compatible taxonomy evidence
- **THEN** the ETF keeps its research rank, is marked observation-only with `taxonomy_bucket_unresolved`, and MUST NOT enter bucket-dependent actionable ranking

#### Scenario: Decision data is ineligible
- **WHEN** an ETF lacks a complete finite total-return-adjusted research window or uses raw, stale, estimated, or display-only fallback prices
- **THEN** the ETF has no research rank and the exact score-exclusion reason is recorded

#### Scenario: Action consumer reads an observation-only row
- **WHEN** allocation, rank-derived email selection, or another action consumer receives a ranked observation-only ETF
- **THEN** the action-quality reasons suppress action exactly as before and the research rank cannot override them

### Requirement: Ranking Coverage Layers Are Reported Separately
Each canonical ETF snapshot SHALL expose separately the authoritative-universe count, decision-data count, score-eligible count, research-ranked count, action-quality-eligible count, observation-only ranked count, and actionable-ranked count, including stable reason counts for each exclusion or downgrade layer.

#### Scenario: Quality gates remove action eligibility only
- **WHEN** one or more score-eligible ETFs fail liquidity or taxonomy quality gates
- **THEN** score and research coverage remain unchanged while action-quality and actionable coverage decrease and observation-only coverage increases

#### Scenario: Score construction fails
- **WHEN** a required adjusted-daily input is missing or non-finite
- **THEN** both score and research coverage decrease and the ETF is not counted as observation-only ranked

#### Scenario: Summary evidence is returned
- **WHEN** a normal ranking page requests compact snapshot metadata
- **THEN** row-level exclusion arrays are omitted while all layer counts, coverage ratios, and aggregated reasons remain available

### Requirement: Legacy Canonical Snapshots Remain Readable During Migration
The system SHALL continue to read and display a previously published compatible canonical snapshot until a broadened research snapshot for the required trade date is successfully published.

#### Scenario: New materialization has not completed
- **WHEN** code supporting broadened research discovery is deployed but only the prior compatible snapshot exists
- **THEN** the prior snapshot remains readable and no empty ranking is introduced solely by the policy migration

#### Scenario: New snapshot is published
- **WHEN** the broadened snapshot passes the unchanged readiness and publication checks
- **THEN** canonical selection advances atomically and preserves the prior publication as a rollback target
