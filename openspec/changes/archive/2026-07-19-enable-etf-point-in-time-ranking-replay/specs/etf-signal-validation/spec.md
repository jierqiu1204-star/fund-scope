## ADDED Requirements

### Requirement: Validation Ranking Sources Are Explicit And Isolated
Every ETF ranking validation run SHALL declare `ranking_source_kind=production_published` or `ranking_source_kind=research_replay`, SHALL validate the corresponding immutable source identity, and MUST NOT merge dates, samples, coverage, confidence intervals, or evidence status across the two source kinds.

#### Scenario: Published production ranking is validated
- **WHEN** `ranking_source_kind=production_published`
- **THEN** validation requires a real published full-scope source run id, exact production score field/version/hash, and compatible total-return-adjusted outcomes

#### Scenario: Research replay ranking is validated
- **WHEN** `ranking_source_kind=research_replay`
- **THEN** validation requires an immutable replay run key, replay contract/input/universe hashes, and finite declared `research_score` without falling back to legacy `total_score`

#### Scenario: Source kinds share future prices
- **WHEN** production and research sources use some of the same future adjusted rows
- **THEN** their independent-date counts, coverage, bootstrap intervals, and evidence classifications remain separate

### Requirement: Validation Reports Stable Readiness Reasons
ETF signal validation SHALL return explicit machine-readable readiness and exclusion reasons instead of a context-free `N/A` and SHALL keep pending, insufficient, incompatible, and unavailable states distinct.

#### Scenario: No valid historical source exists
- **WHEN** production snapshots are absent and research replay has not been materialized
- **THEN** the response reports `no_production_published_snapshot` and `research_replay_not_materialized` independently

#### Scenario: Research universe cannot be proven
- **WHEN** a replay date lacks sufficient factual point-in-time membership
- **THEN** validation reports `insufficient_point_in_time_universe` and excludes the date rather than using the current ETF list

#### Scenario: Outcome cannot complete
- **WHEN** T+1 adjusted entry, horizon exit, independent dates, or score coverage is missing
- **THEN** validation reports the applicable `future_window_pending`, `missing_adjusted_entry_or_exit`, `insufficient_independent_dates`, or `insufficient_score_coverage` reason
