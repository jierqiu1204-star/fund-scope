## MODIFIED Requirements

### Requirement: ETF Default Display Uses Quality Gates
The system SHALL separate all stored ETFs, observation-only ETFs, the canonical daily research ranking, and the actionable subset by applying deterministic versioned history, freshness, absolute tradability, taxonomy, and execution-quality gates.

#### Scenario: Qualified ETF appears in canonical research rank
- **WHEN** an authoritative-universe ETF has at least 61 point-in-time decision-eligible total-return-adjusted sessions ending on the ranking date, passes the declared absolute-turnover gate, and has a known compatible taxonomy bucket
- **THEN** it is eligible for the canonical daily research ranking even if intraday actionable fields are unavailable

#### Scenario: ETF fails absolute tradability
- **WHEN** an ETF has a finite daily research score but its recent average turnover is missing or below the versioned absolute tradability threshold
- **THEN** it receives no canonical research rank, remains searchable as `observation_only`, and exposes `absolute_tradability_below_threshold`

#### Scenario: ETF taxonomy is unresolved
- **WHEN** an ETF has no compatible asset bucket supported by cutoff-valid taxonomy evidence
- **THEN** it receives no peer-relative or actionable rank, remains searchable, and exposes `taxonomy_bucket_unresolved`

#### Scenario: Short-history ETF is visibly provisional
- **WHEN** an otherwise research-eligible ETF has 61 to 119 eligible adjusted sessions
- **THEN** it is marked `provisional_short_history`, may appear in the research rank, and cannot appear as actionable

#### Scenario: Qualified ETF appears in actionable display
- **WHEN** an ETF has at least 120 eligible adjusted sessions and passes all current actionable score, market-data, provider-health, and risk gates
- **THEN** it may appear in the actionable ranking

#### Scenario: Unqualified ETF remains searchable
- **WHEN** an ETF fails a research or actionable quality gate but remains part of the stored authoritative ETF universe
- **THEN** it remains searchable with its failing surface, quality state, and stable reason shown to the user

### Requirement: Short ETF Frontend Is Operable From The Web
The system SHALL expose a Chinese web interface for daily research ranking, intraday actionable ranking, evidence, tracking, and visual inspection, and SHALL use labels and sort controls that correspond to distinct backend score and surface contracts.

#### Scenario: User opens ETF ranking
- **WHEN** the user opens the ETF mode in `/short-term`
- **THEN** the default surface is labeled `日线研究榜`, the separate execution-qualified surface is labeled `盘中可行动榜`, and each shows its score identity, cutoff, coverage, history tier, and quality state

#### Scenario: Sort options are rendered
- **WHEN** the selected ETF surface exposes only one canonical score order
- **THEN** the workbench MUST NOT present multiple sort labels that resolve to that same score field and order

#### Scenario: Observation-only ETF is viewed
- **WHEN** the user selects the low-liquidity or unresolved-taxonomy observation view
- **THEN** the ETF is visually separated from canonical ranked rows and is not described as an actionable or Top-N candidate

## ADDED Requirements

### Requirement: ETF taxonomy and tracked-underlying facts are auditable
The system SHALL persist versioned ETF taxonomy and tracked-underlying evidence with provider, provider version, source identifier, observed-at cutoff, confidence, rule version, raw evidence hash, and normalized identity.

#### Scenario: Explicit cross-border marker exists
- **WHEN** an ETF name or authoritative product/index metadata contains an explicit overseas jurisdiction or index marker such as Hang Seng or Hong Kong
- **THEN** cross-border classification takes precedence over domestic technology, healthcare, or sector keywords

#### Scenario: Authoritative tracked index is available
- **WHEN** a cutoff-valid authoritative product source identifies the ETF's tracked index
- **THEN** the active membership records the normalized tracked-underlying identity and its evidence provenance

#### Scenario: Only a name similarity exists
- **WHEN** two ETF names appear similar but no authoritative tracked-underlying fact exists
- **THEN** the system MUST NOT claim formal clone identity from the name alone and records the identity as unresolved

#### Scenario: Classification changes
- **WHEN** newer authoritative evidence changes a taxonomy or tracked-underlying fact
- **THEN** the system appends a new version and retains the prior cutoff-valid fact for PIT replay

### Requirement: Canonical research results expose concentration and eligibility
The system SHALL expose canonical rank coverage, observation-only coverage, taxonomy coverage, tracked-underlying coverage, clone counts, theme concentration, and quality exclusion reasons without treating these diagnostics as score bonuses.

#### Scenario: Top N contains repeated exposure
- **WHEN** multiple canonical rows share one tracked underlying or exceed a declared theme concentration diagnostic
- **THEN** the API reports the concentration and may provide a separate diversified presentation while preserving the original score and rank identity

#### Scenario: Clone evidence is incomplete
- **WHEN** tracked-underlying coverage is below the declared activation threshold
- **THEN** clone-aware presentation is labeled incomplete and MUST NOT claim that one-underlying control fully passed
