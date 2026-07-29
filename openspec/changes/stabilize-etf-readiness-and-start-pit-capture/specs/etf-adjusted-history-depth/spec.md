## MODIFIED Requirements

### Requirement: ETF adjusted history accumulates in prioritized bounded lanes
The system SHALL preserve target-date and 61-session readiness priority, SHALL classify target-date and warm-up coverage with the shared `blocked`, `degraded`, and `complete` policy, SHALL start 300-session research-depth provider work only after `complete`, SHALL keep 300/500-session completion at 95 percent, and SHALL treat 500-session depth as lower-priority non-authoritative telemetry.

#### Scenario: Publication prerequisites are blocked
- **WHEN** target-date decision-eligible adjusted coverage is below 95 percent or 61-session score-warmup coverage is below 90 percent
- **THEN** research-depth provider work does not start and the publication-readiness continuation retains priority

#### Scenario: Warm-up coverage supports a degraded preview
- **WHEN** target-date coverage is at least 95 percent and 61-session score coverage is at least 90 percent but below 95 percent
- **THEN** only a provisional eligible-subset research preview is available, the 61-session repair lane retains provider priority, and no 300-session or 500-session provider work starts

#### Scenario: Complete readiness allows primary research depth
- **WHEN** target-date and 61-session coverage are both at least 95 percent and fewer than the required ETFs have 300 eligible adjusted sessions
- **THEN** later due triggers may advance one bounded 300-session continuation without changing the complete snapshot

#### Scenario: Primary research depth is ready
- **WHEN** 300-session coverage reaches 95 percent
- **THEN** later bounded continuations may accumulate 500-session telemetry without changing publication or promotion evidence
