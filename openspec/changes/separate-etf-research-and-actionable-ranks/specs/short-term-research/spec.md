## MODIFIED Requirements

### Requirement: Short-Term Data Sufficiency Gates Replace Long-Term Gates
The system SHALL apply explicit research and actionable data-sufficiency gates suitable for short-term ETF research and SHALL not use the long-term 730-day strategy gate as the blocker for this homepage.

#### Scenario: Less than 20 trading days
- **WHEN** an asset has fewer than 20 usable trading days
- **THEN** it is marked `数据不足` and cannot appear as `短线观察`

#### Scenario: Between 20 and 60 trading days
- **WHEN** an ETF has 20 to 60 usable trading days
- **THEN** the system may display limited trend data but cannot calculate or publish the 60-session research rank

#### Scenario: Between 61 and 119 trading days
- **WHEN** an ETF has 61 to 119 decision-eligible adjusted sessions
- **THEN** it may appear in the daily research rank with `短样本` context and MUST NOT appear in the actionable rank

#### Scenario: Between 120 and 249 trading days
- **WHEN** an ETF has 120 to 249 decision-eligible adjusted sessions
- **THEN** it may pass the actionable history gate if all other data and risk requirements are satisfied

#### Scenario: At least 250 trading days
- **WHEN** an ETF has at least 250 decision-eligible adjusted sessions
- **THEN** the system marks its historical context as relatively complete without automatically increasing its score

### Requirement: ETF Ranking Uses The Unified Short-Term Research Source
The system SHALL use `/api/short-research` as the canonical source for both ETF ranking surfaces and their explanations on the short-term page.

#### Scenario: Short-term ETF list is loaded
- **WHEN** the `/short-term` page loads ETF mode
- **THEN** ranked ETF cards default to the latest cached daily research surface and expose actionable eligibility and action rank separately

#### Scenario: User selects actionable filter
- **WHEN** the user filters the ETF list to actionable items
- **THEN** the page displays only eligible `actionable_rank_v1` rows without reinterpreting research ranks as action ranks

#### Scenario: Legacy short ETF endpoint is called
- **WHEN** a client calls the legacy `/api/short-etf` endpoints
- **THEN** the system preserves compatibility while avoiding a conflicting scoring explanation from being shown as the primary short-term workbench result

## ADDED Requirements

### Requirement: Short-Term Workbench Separates Research Rank From Action Eligibility
The short-term research workbench SHALL clearly distinguish broad daily research ranking from strict actionable eligibility.

#### Scenario: Research rank is available and action rank is unavailable
- **WHEN** an ETF has a valid research row but lacks required intraday action evidence
- **THEN** the card shows its research rank and score together with `暂不可行动` and the specific missing or stale evidence

#### Scenario: Both ranks are available
- **WHEN** an ETF is present on both surfaces
- **THEN** the detail view labels each rank, contract version, as-of timestamp, and history-confidence tier without merging the scores

#### Scenario: Actionable surface has no rows
- **WHEN** provider or market-data health prevents every ETF from passing actionable gates
- **THEN** the research list remains usable and the actionable filter shows a fail-closed unavailable state rather than fallback candidates
