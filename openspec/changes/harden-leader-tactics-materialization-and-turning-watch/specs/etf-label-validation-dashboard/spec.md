## MODIFIED Requirements

### Requirement: Leader-tactics candidates are filterable by formula and state
The dashboard SHALL offer formula filters for all, breakout, base launch, and former-leader repair, plus separate state filters for turning watch, preparing, confirmed, and invalidated, and SHALL show deterministic paginated rows without mixing non-actionable observations with candidate lifecycle states.

#### Scenario: User filters turning watches
- **WHEN** the selected state is `turning_watch`
- **THEN** rows are labeled `转强观察（研究）` and show resolved theme, theme source, passed gates, remaining blockers, threshold distance, signal date, and cutoff without buy or confirmation wording

#### Scenario: User filters confirmed base-launch candidates
- **WHEN** the selected formula is base launch and the selected state is confirmed
- **THEN** only matching confirmed rows are displayed with code, name, theme, score, signal date, confirmation date, and cutoff

#### Scenario: Filtered result is empty
- **WHEN** no row matches the selected filters
- **THEN** the page shows a Chinese empty state with candidate, watch, and exclusion counts rather than implying a loading failure

## ADDED Requirements

### Requirement: Materialization status distinguishes universes and stages
The dashboard SHALL keep ETF and A-share requests isolated, show the selected universe's manifest and cutoff, show A-share feature/group progress when available, and state that both V2 research surfaces are isolated from the ETF comprehensive ranking.

#### Scenario: A-share materialization waits for memory
- **WHEN** the latest A-share run is paused by the declared memory headroom gate
- **THEN** the workflow records current and required headroom, while the page retains the completed feature count and current stage without exposing a partial cohort

#### Scenario: ETF V2 is materialized independently
- **WHEN** an ETF research manifest exists while the comprehensive ranking has another manifest identity
- **THEN** the ETF research identity is shown only under the ETF selection and no relationship stronger than research isolation is implied
