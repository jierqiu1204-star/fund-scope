## ADDED Requirements

### Requirement: Short-Term Workbench Displays Catalyst Shadow Separately
The short-term research workbench SHALL display verified catalyst event facts, source coverage, receipt times, direct or proxy mappings, and limitations in a separate research-only section.

#### Scenario: Selected ETF has an active direct event
- **WHEN** the latest completed shadow snapshot contains a verified direct-theme event for the selected ETF
- **THEN** the detail view shows event type, factual summary, source link, published time, received time, effective period, direction, and shadow-only explanation

#### Scenario: Selected ETF uses a proxy mapping
- **WHEN** catalyst context reaches the ETF through a proxy theme
- **THEN** the detail view labels the proxy mapping and MUST NOT describe it as a direct ETF catalyst

#### Scenario: No event was observed
- **WHEN** catalyst coverage is `observed_none`
- **THEN** the detail view says no qualifying event was observed in successfully checked sources

#### Scenario: Catalyst sources are unavailable
- **WHEN** catalyst coverage is `unavailable`
- **THEN** the detail view shows a source-coverage limitation and MUST NOT describe the state as neutral or event-free

#### Scenario: Catalyst shadow is displayed beside ranking
- **WHEN** research or actionable ranks are visible
- **THEN** the UI states that catalyst facts do not change either score, rank, allocation, tracked-position action, or email trigger
