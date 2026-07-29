## MODIFIED Requirements

### Requirement: Post-close ETF publication readiness is coordinated by bounded continuations
The system SHALL measure target-session decision-data freshness and 61-session score warm-up coverage separately, classify one shared readiness state, run at most one bounded continuation when required, allow only an explicitly provisional research preview in `degraded`, and generate a complete dual-ranking snapshot only in `complete`.

#### Scenario: Daily freshness is below the gate
- **WHEN** fewer than 95 percent of the authoritative ETF universe have decision-eligible `total_return_adjusted` data for the target trade date
- **THEN** readiness is `blocked`, the coordinator may run one bounded publication-readiness slice, and no target-date ranking preview or complete publication is produced

#### Scenario: Warm-up is below the preview gate
- **WHEN** target-date adjusted coverage is at least 95 percent but fewer than 90 percent of the universe have 61 eligible exchange sessions
- **THEN** readiness is `blocked`, the coordinator prioritizes warm-up gaps, and no ranking preview or complete publication is produced

#### Scenario: Warm-up supports only a provisional research preview
- **WHEN** target-date adjusted coverage is at least 95 percent and 61-session score-eligible coverage is at least 90 percent but below 95 percent
- **THEN** readiness is `degraded`, only score-eligible ETFs may appear in a provisional research preview, and no complete dual publication or PIT capture is allowed

#### Scenario: Both complete coverage gates pass
- **WHEN** target-date adjusted coverage and 61-session score-eligible coverage are both at least 95 percent
- **THEN** readiness is `complete` and the coordinator generates and publication-validates one complete dual-ranking snapshot without running another synchronization slice

#### Scenario: Authoritative universe is unavailable
- **WHEN** the target trade date lacks a successful authoritative universe receipt
- **THEN** the coordinator records `universe_not_authoritative`, starts no history provider work, and produces no target-date ranking

## ADDED Requirements

### Requirement: Publication memory admission uses current RSS
The system SHALL control provider and page admission using current process RSS, SHALL keep process-lifetime high-water RSS separate from current and slice-peak RSS, and SHALL NOT let a released historical memory spike permanently block later continuations.

#### Scenario: Lifetime peak exceeds the limit but current RSS recovered
- **WHEN** lifetime peak RSS is above 512 MiB while current RSS is at or below 512 MiB and all other budgets permit work
- **THEN** the worker may admit the next bounded request and records lifetime peak only as telemetry

#### Scenario: Current RSS exceeds the limit
- **WHEN** current process RSS exceeds 512 MiB before a provider request or page
- **THEN** the worker admits no more work, persists the last complete checkpoint, and returns `rss_limit`

#### Scenario: Current RSS cannot be measured
- **WHEN** the production worker cannot obtain a valid current-RSS value
- **THEN** it fails provider admission closed with a stable resource-unavailable reason instead of treating zero as unlimited headroom

#### Scenario: Resource telemetry is inspected
- **WHEN** a bounded slice finishes
- **THEN** compact telemetry distinguishes baseline RSS, current RSS, slice peak current RSS, lifetime peak RSS, and RSS delta

### Requirement: Publication scheduling avoids no-op run churn
The system SHALL evaluate cadence, lease, prerequisite, and complete-publication state before creating provider clients or persisted tracked work, while preserving one auditable record for each due slice, materialization, publication, or meaningful blocker.

#### Scenario: Catch-up cadence is not due
- **WHEN** a scheduler tick occurs before the current conservative or maximum cadence is due
- **THEN** it starts no tracked slice, provider client, or concurrent worker

#### Scenario: Complete snapshot already exists
- **WHEN** a valid complete dual snapshot exists for the required trade date and contract
- **THEN** later publication-readiness ticks start no provider work and return an idempotent complete state

#### Scenario: A due slice is executed
- **WHEN** readiness requires catch-up and cadence, lease, trading-session, universe, and resource prerequisites pass
- **THEN** exactly one bounded tracked slice is created with compact progress and provider-health evidence
