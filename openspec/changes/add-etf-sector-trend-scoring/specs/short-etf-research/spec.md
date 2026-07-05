## ADDED Requirements

### Requirement: Short ETF Signals Include Sector Trend Evidence
Short ETF signal items SHALL include sector trend evidence when a real sector trend score is available, while preserving the original technical score and risk labels.

#### Scenario: Signal item stores sector trend evidence
- **WHEN** a successful ETF signal run is generated for an ETF whose theme has valid peer trend evidence
- **THEN** the persisted signal item includes `sector_trend_score`, `sector_trend_label`, `sector_peer_count`, `sector_trend_summary`, and `opportunity_breakdown`

#### Scenario: Detail score matches latest signal cache
- **WHEN** the user opens an ETF detail page after a successful ETF signal run
- **THEN** the detail response returns the cached technical, sector trend, catalyst, event heat, and opportunity scores from the latest signal item for that ETF

### Requirement: Short ETF Opportunity Sorting Uses Real Comprehensive Scores
The short ETF ranking API SHALL sort by real available `opportunity_score` values and SHALL not use neutral fallback values for unavailable comprehensive scores.

#### Scenario: Available opportunity scores sort first
- **WHEN** the client requests ETF assets with `sort=opportunity`
- **THEN** ETFs with numeric `opportunity_score` are sorted by that score before ETFs whose comprehensive score is unavailable

#### Scenario: Missing score is shown as unavailable
- **WHEN** an ETF lacks sector trend and catalyst evidence
- **THEN** the API returns `opportunity_score = null` and a readable unavailable label instead of `50`, `60`, or another fallback score

### Requirement: Robotics And Innovative Drug Themes Are Searchable ETF Themes
The system SHALL classify robotics and innovative drug ETFs as distinct ETF themes for filtering, sector trend grouping, and signal display.

#### Scenario: Robotics ETF is classified as robotics
- **WHEN** an ETF name, theme tag, investment direction, or stored metadata contains robotics-related terms such as `机器人`, `具身智能`, `人形机器人`, or `工业机器人`
- **THEN** the ETF theme profile identifies `机器人` as the primary theme or a directly searchable theme

#### Scenario: Innovative drug ETF is classified as innovative drug
- **WHEN** an ETF name, theme tag, investment direction, or stored metadata contains innovative-drug-related terms such as `创新药`, `生物药`, `生物医药`, `港股创新药`, or `医药创新`
- **THEN** the ETF theme profile identifies `创新药` as the primary theme or a directly searchable theme

#### Scenario: Innovative drug has no fake catalyst
- **WHEN** an innovative drug ETF has valid sector trend evidence but no verified theme catalyst event
- **THEN** the ETF may receive an opportunity score from technical plus sector trend evidence, while catalyst and event heat fields remain `null`

### Requirement: Short ETF Frontend Shows Sector Trend And Missing Data Clearly
The `/short-term` ETF interface SHALL display sector trend scores and comprehensive score status in Chinese without formatting missing values as numeric scores.

#### Scenario: Sector trend appears in list and detail
- **WHEN** an ETF has a numeric sector trend score
- **THEN** the list card or detail panel shows the board trend label, score, peer count, and summary near the comprehensive attention section

#### Scenario: Missing sector trend shows unavailable text
- **WHEN** `sector_trend_score` is `null`
- **THEN** the frontend shows `暂无板块趋势数据` or an equivalent unavailable message instead of a number

#### Scenario: Score weighting version is visible
- **WHEN** an ETF has a numeric `opportunity_score`
- **THEN** the frontend identifies whether the score used full evidence, technical plus sector trend, or technical plus catalyst evidence
