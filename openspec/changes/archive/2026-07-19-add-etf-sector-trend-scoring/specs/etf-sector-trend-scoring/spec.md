## ADDED Requirements

### Requirement: ETF Sector Trend Scores Are Computed From Theme Peers
The system SHALL compute ETF sector trend scores from ETFs that share a classified primary theme or theme group, using only current signal-run metrics with sufficient daily-market data.

#### Scenario: Theme peers produce a sector trend score
- **WHEN** an ETF signal run contains multiple ETFs with the same classified theme and sufficient metrics for recent returns, moving-average position, turnover, and data reliability
- **THEN** the system produces a numeric `sector_trend_score`, `sector_trend_label`, `sector_peer_count`, and `sector_trend_summary` for ETFs in that theme

#### Scenario: Sector trend uses peer evidence rather than a single ETF
- **WHEN** one ETF has strong 5-day performance but its theme peers do not show broad participation
- **THEN** the system SHALL limit the sector trend score to reflect weak peer breadth rather than treating the single ETF move as a strong board trend

### Requirement: Missing Sector Trend Data Is Not Faked
The system SHALL return unavailable sector trend fields as `null` with a readable status or reason instead of substituting neutral fallback scores.

#### Scenario: Insufficient peer coverage
- **WHEN** a theme has too few valid peer ETFs or most peer metrics are stale, insufficient, or unusable
- **THEN** `sector_trend_score` is `null`, `sector_trend_status` identifies the data gap, and no neutral board-trend score is exposed

#### Scenario: Unclassified ETF has no sector trend
- **WHEN** an ETF cannot be classified into a usable theme
- **THEN** the ETF does not receive a sector trend score and the API exposes a no-theme or unavailable reason

### Requirement: Opportunity Score Uses Available Evidence Weights
The system SHALL compute `opportunity_score` only from real available evidence and SHALL expose the weighting version used for each ETF.

#### Scenario: Full evidence score
- **WHEN** technical score, sector trend score, theme catalyst score, and event heat score are all available
- **THEN** `opportunity_score` is computed from technical 60%, sector trend 20%, catalyst 15%, and event heat 5%

#### Scenario: Catalyst unavailable but sector trend available
- **WHEN** technical score and sector trend score are available but theme catalyst and event heat are unavailable
- **THEN** `opportunity_score` is computed from technical 75% and sector trend 25%, and `opportunity_breakdown` identifies the degraded weighting version

#### Scenario: No non-technical evidence
- **WHEN** only technical score is available and both sector trend and catalyst evidence are unavailable
- **THEN** `opportunity_score` is `null` and the ETF is not presented as having a comprehensive attention score

### Requirement: Sector Trend Cannot Override Risk Labels
The system SHALL preserve existing ETF risk and entry-timing labels regardless of sector trend strength.

#### Scenario: Strong board trend with chase risk
- **WHEN** an ETF has a strong sector trend score but its entry timing is `冲高别追`
- **THEN** the ETF retains the chase-risk label and the opportunity label communicates that the theme is strong but the buy point must wait

#### Scenario: Strong board trend with stale data
- **WHEN** an ETF has stale or insufficient own-market data
- **THEN** the ETF does not receive a stronger actionable conclusion because of peer board strength
