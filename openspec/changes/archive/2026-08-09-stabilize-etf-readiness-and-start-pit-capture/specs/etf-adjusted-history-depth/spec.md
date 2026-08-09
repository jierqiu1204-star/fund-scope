## MODIFIED Requirements

### Requirement: ETF adjusted history accumulates in prioritized bounded lanes
The system SHALL preserve target-date and 61-session readiness priority, SHALL classify new policy-v2 target-date and warm-up coverage as `blocked` or `complete`, SHALL preserve legacy v1 `degraded` evidence, SHALL start 300-session research-depth provider work only after `complete`, SHALL keep 300/500-session completion at 95 percent, and SHALL treat 500-session depth as lower-priority non-authoritative telemetry.

#### Scenario: Publication prerequisites are blocked
- **WHEN** target-date decision-eligible adjusted coverage is below 95 percent or 61-session score-warmup coverage is below 90 percent
- **THEN** research-depth provider work does not start and the publication-readiness continuation retains priority

#### Scenario: Warm-up coverage reaches policy-v2 completion
- **WHEN** target-date coverage is at least 95 percent and 61-session score coverage is at least 90 percent but below 95 percent
- **THEN** the eligible subset is complete under policy v2 and later due triggers may start the 300-session lane

#### Scenario: Complete readiness allows primary research depth
- **WHEN** target-date coverage is at least 95 percent, 61-session coverage is at least 90 percent, and fewer than the required ETFs have 300 eligible adjusted sessions
- **THEN** later due triggers may advance one bounded 300-session continuation without changing the complete snapshot

#### Scenario: Primary research depth is ready
- **WHEN** 300-session coverage reaches 95 percent
- **THEN** later bounded continuations may accumulate 500-session telemetry without changing publication or promotion evidence
