## MODIFIED Requirements

### Requirement: ETF Market Data Is Synchronizable
The system SHALL synchronize ETF daily market data including date, open, high, low, close, volume, turnover, percentage change, adjusted research value, price basis, provider version, adjustment version, source timestamp, and decision eligibility for the dynamic short-term ETF universe.

#### Scenario: Data sync stores daily prices
- **WHEN** the user or post-close coordinator runs ETF data sync
- **THEN** the system stores or updates daily ETF price rows without duplicating existing code/date records and preserves stronger decision-eligible provenance over weaker raw-only data

#### Scenario: Data source failure is reported
- **WHEN** one ETF data source request fails during synchronization
- **THEN** the task records the ETF code and bounded readable error reason while continuing with other eligible candidates within the slice budget

#### Scenario: Large universe sync is batched
- **WHEN** the system synchronizes a large ETF universe
- **THEN** one worker processes stable code/date pages under code, row, memory, SQL, provider, and 60-second time limits and records a durable resumable checkpoint

#### Scenario: Higher priority ETFs update first
- **WHEN** the daily ETF publication-readiness task runs
- **THEN** ETFs missing the target trade date are processed before 61-session warm-up gaps, and tracked or default-display ETFs are prioritized within the same gap class without starving other authoritative-universe members

#### Scenario: Publication data retains priority
- **WHEN** target-date adjusted coverage is below 95 percent or 61-session adjusted coverage is below the configured publication threshold
- **THEN** no 300-session or 500-session research-depth provider work starts

#### Scenario: Research history accumulates after publication readiness
- **WHEN** both publication gates pass
- **THEN** scheduled serial slices advance 300 adjusted sessions first and only then advance non-authoritative 500-session telemetry
- **AND** both research-depth completion gates remain 95 percent

#### Scenario: Current-data request also covers warm-up
- **WHEN** an ETF is missing target-date adjusted data
- **THEN** the bounded request may include the recent 61-session date window so one provider round trip can persist current and warm-up data while the two readiness lanes remain independently measured

#### Scenario: Continuation is resumed
- **WHEN** a compatible bounded synchronization slice previously ended as partial
- **THEN** the next slice resumes idempotently from persisted pages and rotation state instead of restarting an unbounded universe scan

## ADDED Requirements

### Requirement: ETF ranking publication requires current and score-ready adjusted data
The system SHALL publish a full ETF ranking only when at least 95 percent of the authoritative target-date universe have decision-eligible `total_return_adjusted` data for the target session and at least 95 percent are score-eligible with 61 exchange sessions, and SHALL otherwise return an explicit waiting state.

#### Scenario: Decision-data coverage is insufficient
- **WHEN** target-date decision-eligible adjusted coverage is below 95 percent
- **THEN** the system publishes no ranking and reports the current coverage blocker

#### Scenario: Score coverage is insufficient
- **WHEN** decision-data coverage reaches 95 percent but 61-session score-eligible coverage remains below 95 percent
- **THEN** the system publishes no degraded ranking and reports the warm-up blocker

#### Scenario: Raw fallback rows exist
- **WHEN** Sina, efinance, intraday snapshot, stale cache, estimated, or other raw-only rows exist without complete adjusted provenance
- **THEN** those rows remain display-only or unavailable and MUST NOT increase either publication coverage ratio

#### Scenario: Both gates pass
- **WHEN** both registered 95 percent gates pass for the same target trade date and authoritative universe
- **THEN** the system may materialize and publication-validate the full dual-ranking snapshot using only decision-eligible adjusted inputs
