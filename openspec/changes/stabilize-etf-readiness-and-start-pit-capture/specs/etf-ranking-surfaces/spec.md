## MODIFIED Requirements

### Requirement: ETF Ranking Surfaces Have Distinct Contracts
The system SHALL expose research and actionable ranking surfaces with distinct contract IDs, score fields, versions, hashes, ordered ranks, eligibility states, coverage summaries, as-of timestamps, and readiness state, and SHALL distinguish a provisional degraded research preview from a complete dual-ranking publication.

#### Scenario: ETF is present on both complete surfaces
- **WHEN** readiness is `complete` and an ETF satisfies both daily research and actionable eligibility
- **THEN** the system records its `daily_reconstructable_v1` research score and rank separately from its `actionable_rank_v1` score and rank

#### Scenario: ETF is research-only in a complete snapshot
- **WHEN** readiness is `complete` and an ETF has eligible adjusted daily history but lacks a mandatory actionable input
- **THEN** the ETF remains ranked on the research surface and has no actionable rank, with the actionable exclusion reason recorded

#### Scenario: ETF is available only in a degraded preview
- **WHEN** readiness is `degraded` and an ETF has 61 eligible adjusted sessions
- **THEN** it may appear only on the provisional research preview with the degraded policy version and MUST have no complete or actionable rank

#### Scenario: Contract identities cannot be substituted
- **WHEN** a consumer requests or validates one ranking surface or readiness state
- **THEN** the system MUST NOT satisfy that request with another surface's score, rank, version, state, or hash

### Requirement: Ranking Consumers Respect Surface Boundaries
The system SHALL use complete or provisional research surfaces only for discovery and explanation, SHALL visibly identify provisional research, and SHALL use only complete actionable rows for portfolio allocation or rank-derived candidate email selection.

#### Scenario: User browses a complete ETF universe
- **WHEN** the `/short-term` workbench loads a complete current research surface
- **THEN** it shows the cached research rank, complete snapshot date and coverage, and action eligibility separately

#### Scenario: User browses a provisional ETF universe
- **WHEN** the current target date has a degraded research preview but no complete snapshot
- **THEN** the workbench may show the eligible research rows with a persistent provisional banner, coverage, cutoff, and exclusions and MUST NOT imply actionability or complete validation

#### Scenario: Portfolio is generated
- **WHEN** ETF observation allocation selects candidate ETFs
- **THEN** every non-zero-weight candidate references an eligible complete `actionable_rank_v1` row from the required as-of context

#### Scenario: Email candidate is selected by rank
- **WHEN** a workflow proposes an email because an ETF belongs to a top-ranked candidate set
- **THEN** the ETF MUST have an eligible complete actionable row and matching contract context

#### Scenario: Tracked-position risk alert is evaluated
- **WHEN** an existing holding triggers its independent risk-alert lifecycle
- **THEN** complete or provisional research-rank membership neither creates nor suppresses that alert, while the alert's own decision-data gates still apply
